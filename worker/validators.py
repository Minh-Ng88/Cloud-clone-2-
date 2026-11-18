import os
from pypdf import PdfReader

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

def validate_input_file(file_path: str, expected_ext: str) -> None:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        raise ValueError("File rỗng (0 bytes).")
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"Dung lượng file vượt quá giới hạn 10 MB ({file_size} bytes).")

    ext = expected_ext.lower().lstrip(".")
    with open(file_path, "rb") as f:
        header = f.read(16)

    if ext == "png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("File không phải định dạng PNG thật (sai magic bytes).")
    elif ext in ("jpg", "jpeg"):
        if not header.startswith(b"\xff\xd8\xff"):
            raise ValueError("File không phải định dạng JPG/JPEG thật (sai magic bytes).")
    elif ext in ("txt", "md"):
        with open(file_path, "rb") as f:
            sample = f.read(4096)
        if b"\x00" in sample:
            raise ValueError(f"File .{ext} chứa dữ liệu nhị phân, không phải văn bản hợp lệ.")
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"File .{ext} không đúng chuẩn mã hóa UTF-8.")
    else:
        raise ValueError(f"Định dạng không được hỗ trợ: {ext}")


def validate_output_pdf(pdf_path: str) -> None:
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise RuntimeError("File PDF đầu ra không tồn tại hoặc bị rỗng.")

    with open(pdf_path, "rb") as f:
        if not f.read(5).startswith(b"%PDF-"):
            raise RuntimeError("File đầu ra không đúng cấu trúc PDF (%PDF-).")

    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) < 1:
            raise RuntimeError("File PDF không có trang nào.")
    except Exception as e:
        raise RuntimeError(f"File PDF bị lỗi cấu trúc, không thể đọc: {e}")