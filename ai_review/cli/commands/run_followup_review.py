from ai_review.services.review.service import ReviewService


async def run_followup_review_command() -> None:
    async with ReviewService() as review_service:
        await review_service.run_followup_review()
        review_service.report_total_cost()
