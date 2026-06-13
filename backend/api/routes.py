import logging
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from backend.api.auth import get_current_user
from backend.models.schemas import (
    AnalysisResponse,
    ComponentScores,
    JDComparison,
    SkillValidationDetails,
)

logger = logging.getLogger("ats_resume_scorer")

router = APIRouter(prefix="/api/v1", tags=["Analysis"])


def _clean(text: str) -> str:
    for prefix in ("✅", "🌟", "❌", "⚠️", "📝", "🔴", "🟡", "🟢", "🟠", "👍"):
        text = text.lstrip(prefix)
    return text.strip()


@router.post("/analyze-resume", response_model=AnalysisResponse)
async def analyze_resume(
    request: Request,
    resume: UploadFile = File(...),
    job_description: str = Form(""),
    user_id: str = Depends(get_current_user),
):
    nlp = request.app.state.nlp
    embedder = request.app.state.embedder

    # -----------------------------
    # 1. SAFE FILE READING
    # -----------------------------
    try:
        file_bytes = await resume.read()

        if not file_bytes:
            raise HTTPException(
                status_code=422,
                detail="Empty resume file uploaded",
            )

        filename = resume.filename or "resume"

        logger.info(
            f"Received resume: {filename}, size={len(file_bytes)} bytes"
        )

    except Exception as exc:
        logger.error(f"File read failed: {exc}")
        raise HTTPException(
            status_code=422,
            detail=f"Could not read uploaded file: {str(exc)}",
        )

    # -----------------------------
    # 2. SAFE PARSING
    # -----------------------------
    try:
        from backend.services.resume_parser import parse_resume_file

        resume_text, _metadata = parse_resume_file(file_bytes, filename)

        if not resume_text or len(resume_text.strip()) < 20:
            raise HTTPException(
                status_code=422,
                detail="Could not extract meaningful text from resume",
            )

        logger.info(
            f"Parsed resume successfully: {len(resume_text)} characters"
        )

    except Exception as exc:
        logger.error(f"Resume parsing failed: {exc}")
        raise HTTPException(
            status_code=422,
            detail=f"Resume parsing failed: {str(exc)}",
        )

    # -----------------------------
    # 3. FULL ANALYSIS PIPELINE
    # -----------------------------
    try:
        from backend.services.resume_analyzer import analyze_full_resume

        result = analyze_full_resume(
            resume_text=resume_text,
            nlp=nlp,
            embedder=embedder,
            job_description=job_description,
        )

    except Exception as exc:
        logger.error(f"Analysis pipeline failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis pipeline failed: {str(exc)}",
        )

    # -----------------------------
    # 4. JD COMPARISON
    # -----------------------------
    jd_comparison_result = None

    if result.get("jd_comparison"):
        jd = result["jd_comparison"]

        jd_comparison_result = JDComparison(
            match_percentage=round(float(jd.get("match_percentage", 0.0)), 1),
            semantic_similarity=round(float(jd.get("semantic_similarity", 0.0)), 3),
            matched_keywords=jd.get("matched_keywords", [])[:20],
            missing_keywords=jd.get("missing_keywords", [])[:15],
            skills_gap=jd.get("skills_gap", [])[:10],
        )

    # -----------------------------
    # 5. SKILL VALIDATION
    # -----------------------------
    svd_raw = result.get("skill_validation_details") or {}

    skill_val_details = SkillValidationDetails(
        validated=svd_raw.get("validated", []),
        unvalidated=svd_raw.get("unvalidated", []),
        total=svd_raw.get("total", 0),
        validated_count=svd_raw.get("validated_count", 0),
        validation_pct=svd_raw.get("validation_pct", 0.0),
    )

    # -----------------------------
    # 6. RESPONSE BUILD
    # -----------------------------
    response = AnalysisResponse(
        ATS_score=result.get("ats_score", 0),

        component_scores=ComponentScores(**result.get("component_scores", {})),

        issues_summary=result.get("issues_summary", []),
        detailed_feedback=result.get("detailed_feedback", []),

        jd_match_analysis=jd_comparison_result,
        skill_validation_details=skill_val_details,

        ats_score=result.get("ats_score", 0),

        keyword_match=jd_comparison_result.match_percentage
        if jd_comparison_result
        else 0.0,

        missing_keywords=result.get("missing_keywords", []),
        matched_keywords=result.get("matched_keywords", []),

        skills=list(result.get("skills", [])[:20]),

        jd_comparison=jd_comparison_result,
        interpretation=result.get("interpretation", ""),
    )

    # -----------------------------
    # 7. SAVE HISTORY (NON-BLOCKING)
    # -----------------------------
    try:
        from backend.database.supabase_db import save_analysis

        await save_analysis(user_id, filename, result)

    except Exception as exc:
        logger.warning(f"History save failed (non-blocking): {exc}")

    return response


# -----------------------------
# HEALTH CHECK
# -----------------------------
@router.get("/health")
async def health_check(request: Request):
    return {
        "status": "healthy",
        "nlp_loaded": request.app.state.nlp is not None,
        "embedder_loaded": request.app.state.embedder is not None,
    }


# -----------------------------
# HISTORY
# -----------------------------
@router.get("/history")
async def get_history(user_id: str = Depends(get_current_user)):
    from backend.database.supabase_db import get_user_history

    try:
        return await get_user_history(user_id)
    except Exception as exc:
        logger.error(f"History fetch failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Could not load history: {exc}",
        )


@router.delete("/history/{analysis_id}")
async def delete_history_entry(
    analysis_id: str,
    user_id: str = Depends(get_current_user),
):
    from backend.database.supabase_db import delete_analysis

    try:
        success = await delete_analysis(analysis_id, user_id)

        if not success:
            raise HTTPException(
                status_code=404,
                detail="Analysis not found or not owned by this user.",
            )

        return {"status": "deleted", "id": analysis_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"History delete failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# -----------------------------
# PDF GENERATION
# -----------------------------
@router.post("/generate-pdf")
async def generate_pdf(
    data: AnalysisResponse,
    user_id: str = Depends(get_current_user),
):
    from backend.services.report_generator import generate_html_reports
    from backend.services.pdf_export import generate_combined_pdf

    try:
        html_docs = generate_html_reports(data.model_dump())
        pdf_bytes = generate_combined_pdf(html_docs)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=ats_report.pdf"
            },
        )

    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# HISTORY PDF
# -----------------------------
@router.get("/history/{analysis_id}/pdf")
async def generate_history_pdf(
    analysis_id: str,
    user_id: str = Depends(get_current_user),
):
    from backend.database.supabase_db import get_user_history
    from backend.services.report_generator import generate_html_reports
    from backend.services.pdf_export import generate_combined_pdf

    history = await get_user_history(user_id)

    analysis_data = next(
        (item["analysis_result"] for item in history if item["id"] == analysis_id),
        None,
    )

    if not analysis_data:
        raise HTTPException(status_code=404, detail="Analysis not found")

    try:
        html_docs = generate_html_reports(analysis_data)
        pdf_bytes = generate_combined_pdf(html_docs)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=ats_report_{analysis_id}.pdf"
            },
        )

    except Exception as e:
        logger.error(f"History PDF failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))