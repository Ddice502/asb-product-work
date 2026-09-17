import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from paddleocr import PaddleOCR


APP_VERSION = "1.0.0"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "26214400"))
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}

app = FastAPI(title="Second Brain Camera OCR", version=APP_VERSION)
ocr_model: PaddleOCR | None = None
model_error = ""
inference_lock = asyncio.Lock()


@app.on_event("startup")
def load_model() -> None:
    global ocr_model, model_error
    try:
        ocr_model = PaddleOCR(
            text_detection_model_name=os.getenv(
                "PADDLE_TEXT_DETECTION_MODEL", "PP-OCRv5_mobile_det"
            ),
            text_recognition_model_name=os.getenv(
                "PADDLE_TEXT_RECOGNITION_MODEL", "PP-OCRv5_mobile_rec"
            ),
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            use_textline_orientation=True,
            device="cpu",
            engine="paddle",
        )
        model_error = ""
    except Exception as error:  # pragma: no cover - startup environment dependent
        ocr_model = None
        model_error = str(error)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ready" if ocr_model is not None else "initializing_or_failed",
        "version": APP_VERSION,
        "model_loaded": ocr_model is not None,
        "model_error": model_error,
    }


def perform_ocr(image_path: str) -> dict[str, Any]:
    if ocr_model is None:
        raise RuntimeError(model_error or "PaddleOCR model is not ready.")

    lines: list[dict[str, Any]] = []
    prediction_results = ocr_model.predict(image_path)

    for prediction in prediction_results:
        payload = prediction.json
        result = payload.get("res", payload) if isinstance(payload, dict) else {}
        texts = result.get("rec_texts", [])
        scores = result.get("rec_scores", [])
        boxes = result.get("rec_boxes", [])
        if boxes is None or len(boxes) == 0:
            boxes = result.get("rec_polys", [])
        if texts is None:
            texts = []
        if scores is None:
            scores = []
        if boxes is None:
            boxes = []

        for index, text in enumerate(texts):
            normalized = str(text or "").strip()
            if not normalized:
                continue
            score = float(scores[index]) if index < len(scores) else 0.0
            box = boxes[index] if index < len(boxes) else []
            if hasattr(box, "tolist"):
                box = box.tolist()
            lines.append(
                {
                    "text": normalized,
                    "confidence": max(0.0, min(1.0, score)),
                    "box": box,
                }
            )

    mean_confidence = (
        sum(item["confidence"] for item in lines) / len(lines) if lines else 0.0
    )
    return {
        "status": "complete",
        "text": "\n".join(item["text"] for item in lines),
        "lines": lines,
        "line_count": len(lines),
        "mean_confidence": mean_confidence,
        "model": {
            "detection": os.getenv(
                "PADDLE_TEXT_DETECTION_MODEL", "PP-OCRv5_mobile_det"
            ),
            "recognition": os.getenv(
                "PADDLE_TEXT_RECOGNITION_MODEL", "PP-OCRv5_mobile_rec"
            ),
        },
    }


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)) -> dict[str, Any]:
    if ocr_model is None:
        raise HTTPException(status_code=503, detail=model_error or "OCR model not ready")

    suffix = Path(file.filename or "capture.jpg").suffix.lower() or ".jpg"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"Unsupported image type: {suffix}")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded image was empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The uploaded image was too large.")

    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            temporary_path = temporary.name

        async with inference_lock:
            return await asyncio.to_thread(perform_ocr, temporary_path)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)
