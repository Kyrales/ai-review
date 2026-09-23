import re
from collections.abc import Sequence

from ai_review.clients.gitflic.schema import (
    GitFlicDiscussion,
    GitFlicMergeRequest,
    GitFlicPipelineStartRequest,
    GitFlicPipelineVariable,
)
from ai_review.services.dispatch.models import (
    DispatchItemResult,
    DispatchMode,
    DispatchReport,
    DispatchSelection,
    classify_dispatch,
)
from ai_review.services.vcs.gitflic.adapter import to_review_thread
from ai_review.services.vcs.types import ReviewThreadSchema


_HEAD = re.compile(r"[0-9a-f]{40}")


def _matches(template: str, branch: str) -> bool:
    expression: list[str] = []
    index = 0
    while index < len(template):
        if template.startswith("**", index):
            expression.append(".*")
            index += 2
        elif template[index] == "*":
            expression.append("[^/]*")
            index += 1
        elif template[index] == "?":
            expression.append("[^/]")
            index += 1
        else:
            expression.append(re.escape(template[index]))
            index += 1
    return re.fullmatch("".join(expression), branch) is not None


def is_safe_ref(value: str) -> bool:
    components = value.split("/")
    return bool(value) and not (
        value.startswith("/")
        or value.endswith("/")
        or ".." in value
        or "//" in value
        or "@{" in value
        or "\\" in value
        or any(char in value for char in " ~^:?*[")
        or any(
            not component
            or component.startswith(".")
            or component.endswith(".")
            or component.endswith(".lock")
            for component in components
        )
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


class GitFlicDispatchService:
    def __init__(
        self,
        client: object,
        owner: str,
        project: str,
        control_ref: str,
        trusted_author_id: str,
    ) -> None:
        self.client = client
        self.owner = owner
        self.project = project
        self.control_ref = control_ref
        self.trusted_author_id = trusted_author_id

    async def _threads(self, merge_request_id: int) -> tuple[ReviewThreadSchema, ...]:
        discussions: Sequence[GitFlicDiscussion] = await self.client.get_discussions_strict(
            self.owner, self.project, merge_request_id
        )
        return tuple(to_review_thread(item) for item in discussions)

    @staticmethod
    def _report(
        items: list[DispatchItemResult],
        selected_count: int,
        *,
        status: str | None = None,
        failure_scope: str | None = None,
        error_code: str | None = None,
    ) -> DispatchReport:
        started = sum(item.outcome == "started" for item in items)
        skipped = sum(item.outcome == "skipped" for item in items)
        failed = sum(item.outcome == "failed" for item in items)
        return DispatchReport(
            status=status or ("partial" if failed else "success"),
            failure_scope=failure_scope or ("item" if failed else "none"),
            error_code=error_code,
            selected_count=selected_count,
            started_count=started,
            skipped_count=skipped,
            failed_count=failed,
            items=tuple(items),
        )

    def _valid_protection(self, protections: Sequence[object]) -> bool:
        matching = [item for item in protections if _matches(item.branchTemplate, self.control_ref)]
        return (
            len(matching) == 1
            and matching[0].branchTemplate == self.control_ref
            and matching[0].allowedToPush in {"ADMINS", "NO_ONE"}
            and matching[0].allowForcePush is False
        )

    @staticmethod
    def _valid_mr(mr: GitFlicMergeRequest, expected_id: int) -> bool:
        return (
            mr.localId == expected_id
            and mr.status is not None
            and mr.status.id == "OPENED"
            and _HEAD.fullmatch(mr.sourceBranch.hash) is not None
            and is_safe_ref(mr.sourceBranch.id)
            and is_safe_ref(mr.targetBranch.id)
        )

    async def run(self, selection: DispatchSelection) -> DispatchReport:
        try:
            if selection.all_open:
                snapshot = await self.client.list_open_mrs_strict(self.owner, self.project)
                if len(snapshot) > 10:
                    return self._report(
                        [], len(snapshot), status="fatal", failure_scope="source",
                        error_code="request_budget_exceeded",
                    )
                ids = tuple(sorted(item.localId for item in snapshot))
            else:
                ids = selection.merge_request_ids
        except Exception:
            return self._report(
                [], 0, status="fatal", failure_scope="source",
                error_code="source_preflight_failed",
            )
        if not ids:
            return self._report([], 0)

        if not is_safe_ref(self.control_ref):
            return self._report(
                [], len(ids), status="fatal", failure_scope="security",
                error_code="unsafe_control_ref",
            )

        try:
            protections = await self.client.list_branch_protections(self.owner, self.project)
        except Exception:
            return self._report(
                [], len(ids), status="fatal", failure_scope="source",
                error_code="protection_lookup_failed",
            )
        if not self._valid_protection(protections):
            return self._report(
                [], len(ids), status="fatal", failure_scope="security",
                error_code="unsafe_control_ref",
            )

        results: list[DispatchItemResult] = []
        for merge_request_id in ids:
            try:
                first = await self.client.get_mr_strict(self.owner, self.project, merge_request_id)
                if not self._valid_mr(first, merge_request_id):
                    if selection.all_open and first.status is not None and first.status.id != "OPENED":
                        results.append(DispatchItemResult(
                            mr_id=merge_request_id, mode=None, outcome="skipped",
                            reason="closed_during_dispatch",
                        ))
                        continue
                    raise ValueError("merge request state changed")
                first_threads = await self._threads(merge_request_id)
                decision = classify_dispatch(
                    first_threads, self.trusted_author_id, first.sourceBranch.hash
                )
                if decision.mode is DispatchMode.NONE:
                    results.append(DispatchItemResult(
                        mr_id=merge_request_id, mode=decision.mode, outcome="skipped",
                        reason=decision.reason,
                    ))
                    continue

                current = await self.client.get_mr_strict(self.owner, self.project, merge_request_id)
                current_threads = await self._threads(merge_request_id)
                if not self._valid_mr(current, merge_request_id):
                    if selection.all_open and current.status is not None and current.status.id != "OPENED":
                        results.append(DispatchItemResult(
                            mr_id=merge_request_id, mode=None, outcome="skipped",
                            reason="closed_during_dispatch",
                        ))
                        continue
                    raise ValueError("merge request state changed")
                decision = classify_dispatch(
                    current_threads, self.trusted_author_id, current.sourceBranch.hash
                )
                if decision.mode is DispatchMode.NONE:
                    results.append(DispatchItemResult(
                        mr_id=merge_request_id, mode=decision.mode, outcome="skipped",
                        reason=decision.reason,
                    ))
                    continue
                request = GitFlicPipelineStartRequest(
                    refName=self.control_ref,
                    variables=(
                        GitFlicPipelineVariable(key="AI_REVIEW_REQUESTED", value="true"),
                        GitFlicPipelineVariable(key="AI_REVIEW_MODE", value=decision.mode.value),
                        GitFlicPipelineVariable(key="AI_REVIEW_MR_ID", value=str(merge_request_id)),
                        GitFlicPipelineVariable(key="AI_REVIEW_HEAD_SHA", value=current.sourceBranch.hash),
                        GitFlicPipelineVariable(key="AI_REVIEW_SOURCE_BRANCH", value=current.sourceBranch.id),
                        GitFlicPipelineVariable(key="AI_REVIEW_TARGET_BRANCH", value=current.targetBranch.id),
                    ),
                )
                pipeline = await self.client.start_pipeline(self.owner, self.project, request)
                results.append(DispatchItemResult(
                    mr_id=merge_request_id, mode=decision.mode, outcome="started",
                    reason=decision.reason, pipeline_id=pipeline.display_id,
                ))
            except Exception:
                results.append(DispatchItemResult(
                    mr_id=merge_request_id, mode=None, outcome="failed",
                    reason="item_processing_failed",
                ))
        return self._report(results, len(ids))
