# 📘 AI Review CI/CD Integration

This folder contains **ready-to-use CI/CD templates** for running AI Review automatically on **Pull Requests** (GitHub,
Bitbucket, Jenkins, Bitbucket, Azure DevOps) or **Merge Requests** (GitLab).

Each example shows how to:

- Install and configure AI Review
- Pass LLM and VCS credentials securely via environment variables
- Trigger inline, summary, or context review commands

---

## ⚙️ Supported CI/CD Providers

| Provider     | Template                                 | Description                                                                    |
|--------------|------------------------------------------|--------------------------------------------------------------------------------|
| GitHub       | [github.yaml](./github.yaml)             | Manual workflow dispatch from Actions tab                                      |
| GitLab       | [gitlab.yaml](./gitlab.yaml)             | Manual job trigger in Merge Request pipelines                                  |
| Jenkins      | [Jenkinsfile](./Jenkinsfile)             | Declarative pipeline with **inline/context/summary** review stages             |
| Bitbucket    | [bitbucket.yaml](./bitbucket.yaml)       | Manual custom pipeline trigger per Pull Request (supports all AI Review modes) |
| Azure DevOps | [azure-devops.yaml](./azure-devops.yaml) | Manual or PR-triggered pipeline in **Azure Pipelines**                         |

---

👉 Choose the template matching your CI system, copy it into your repository, and adjust environment
variables (`OPENAI_API_KEY`, `GITHUB_TOKEN`, `CI_JOB_TOKEN`, `BITBUCKET_TOKEN`, etc.) as needed.

## GitFlic manual dispatch

The `gitflic-dispatch` command is intended for the signed AI Review container.
It needs no host Python installation: the GitFlic runner only supplies
Docker/Cosign and environment secrets. Use `--all` or repeated
`--merge-request-id` options; tokens stay in `AI_REVIEW_GITFLIC_TOKEN` and
optional `AI_REVIEW_GITFLIC_TOKEN2`. The command returns exit 0 on success,
exit 1 for partial item failures, exit 2 for usage/configuration errors, and
exit 3 for source, security, or preflight failures.
