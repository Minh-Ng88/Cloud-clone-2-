import os
import multiprocessing
from fpdf import FPDF
import markdown
from PIL import Image
from validators import validate_input_file, validate_output_pdf

FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "arial.ttf")


def _create_vietnamese_pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if os.path.exists(FONT_PATH):
        pdf.add_font("CustomUnicode", "", FONT_PATH)
        pdf.add_font("CustomUnicode", "B", FONT_PATH)
        pdf.add_font("CustomUnicode", "I", FONT_PATH)
        pdf.set_font("CustomUnicode", size=12)
    else:
        raise FileNotFoundError(f"Thiếu file font Unicode tại {FONT_PATH}")
    return pdf


def txt_to_pdf(input_path: str, output_path: str) -> str:
    validate_input_file(input_path, "txt")
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    pdf = _create_vietnamese_pdf()
    pdf.multi_cell(0, 8, text)
    pdf.output(output_path)

    validate_output_pdf(output_path)
    return output_path


def md_to_pdf(input_path: str, output_path: str) -> str:
    validate_input_file(input_path, "md")
    with open(input_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    html_content = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
    pdf = _create_vietnamese_pdf()
    pdf.write_html(html_content)
    pdf.output(output_path)

    validate_output_pdf(output_path)
    return output_path


def image_to_pdf(input_path: str, output_path: str) -> str:
    ext = os.path.splitext(input_path)[1].lower().lstrip(".")
    validate_input_file(input_path, ext)

    with Image.open(input_path) as img:
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            rgb_img = background
        else:
            rgb_img = img.convert("RGB")

        rgb_img.save(output_path, "PDF", resolution=100.0)

    validate_output_pdf(output_path)
    return output_path


def _worker_target(func, input_path, output_path, queue):
    try:
        res = func(input_path, output_path)
        queue.put(("ok", res))
    except Exception as e:
        queue.put(("err", e))


def convert_with_timeout(input_path: str, output_path: str, timeout_seconds: int = 30) -> str:
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".txt":
        target_func = txt_to_pdf
    elif ext == ".md":
        target_func = md_to_pdf
    elif ext in (".png", ".jpg", ".jpeg"):
        target_func = image_to_pdf
    else:
        raise ValueError(f"Định dạng file không được hỗ trợ: {ext}")

    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_worker_target, args=(target_func, input_path, output_path, q))
    p.start()
    p.join(timeout=timeout_seconds)

    if p.is_alive():
        p.terminate()
        p.join()
        if os.path.exists(output_path):
            os.remove(output_path)
        raise TimeoutError(f"Chuyển đổi vượt quá thời gian cho phép ({timeout_seconds}s).")

    if q.empty():
        raise RuntimeError("Tiến trình chuyển đổi bị ngắt đột ngột mà không trả kết quả.")

    status, payload = q.get()
    if status == "err":
        raise payload
    return payload