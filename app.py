import json
import os
import re
import uuid
import base64
from datetime import datetime
from functools import wraps
from html import escape
from io import BytesIO

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import markdown
except ImportError:
    markdown = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from supabase import create_client
except ImportError:
    create_client = None


app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

if load_dotenv:
    load_dotenv()

app.secret_key = os.getenv("FLASK_SECRET_KEY", "pdf-ai-assistant-demo")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "pdf_files")
supabase_client = None
supabase_auth_client = None


def is_placeholder_value(value):
    return "service_role" in value or "secret key" in value or "APIKey" in value


def is_supabase_configured():
    return bool(
        SUPABASE_URL
        and SUPABASE_SERVICE_ROLE_KEY
        and SUPABASE_BUCKET
        and not is_placeholder_value(SUPABASE_SERVICE_ROLE_KEY)
    )


print("[SUPABASE] URL loaded:", bool(SUPABASE_URL))
print("[SUPABASE] service key loaded:", bool(SUPABASE_SERVICE_ROLE_KEY))
print("[SUPABASE] anon key loaded:", bool(SUPABASE_ANON_KEY))

if (
    SUPABASE_URL
    and SUPABASE_SERVICE_ROLE_KEY
    and not is_placeholder_value(SUPABASE_SERVICE_ROLE_KEY)
    and create_client is not None
):
    try:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as exc:
        print("[SUPABASE] init failed:", exc)
elif is_supabase_configured() and create_client is None:
    print("[SUPABASE] init failed: supabase package is not installed")

if SUPABASE_URL and SUPABASE_ANON_KEY and create_client is not None:
    try:
        supabase_auth_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as exc:
        print("[SUPABASE] auth init failed:", exc)

print("[SUPABASE] enabled:", bool(supabase_client and is_supabase_configured()))


def is_supabase_enabled():
    return bool(supabase_client and is_supabase_configured())


def is_auth_enabled():
    return bool(supabase_auth_client)


def get_current_user_id():
    return session.get("user_id")


def get_current_user_email():
    return session.get("user_email")


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not get_current_user_id():
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def clear_pdf_session():
    session.pop("current_document_id", None)
    session.pop("current_pdf_name", None)
    session.pop("current_pdf_path", None)
    session.pop("current_pdf_stored_filename", None)
    session.pop("current_stored_filename", None)
    session.pop("current_storage_path", None)
    session.pop("original_filename", None)


def is_pdf(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


def is_allowed_pdf_upload(file_storage):
    filename = file_storage.filename or ""
    content_type = file_storage.mimetype or ""
    return is_pdf(filename) and content_type in ("application/pdf", "application/octet-stream")


def make_saved_pdf_filename():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"{timestamp}_{short_id}.pdf"


def get_file_index_path():
    return os.path.join(get_upload_folder(), "file_index.json")


def load_file_index():
    index_path = get_file_index_path()

    if not os.path.isfile(index_path):
        return {}

    try:
        with open(index_path, "r", encoding="utf-8") as index_file:
            data = json.load(index_file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def save_file_index(file_index):
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    with open(get_file_index_path(), "w", encoding="utf-8") as index_file:
        json.dump(file_index, index_file, ensure_ascii=False, indent=2)


def set_current_local_pdf(original_filename, stored_filename, file_path):
    session["current_pdf_name"] = original_filename
    session["current_pdf_path"] = file_path
    session["current_pdf_stored_filename"] = stored_filename
    session["current_stored_filename"] = stored_filename
    session["original_filename"] = original_filename
    session.pop("current_storage_path", None)


def set_current_supabase_pdf(original_filename, stored_filename, storage_path):
    session["current_pdf_name"] = original_filename
    session["current_stored_filename"] = stored_filename
    session["current_pdf_stored_filename"] = stored_filename
    session["current_storage_path"] = storage_path
    session["original_filename"] = original_filename
    session.pop("current_pdf_path", None)


def clear_current_pdf_session():
    session.pop("current_pdf_name", None)
    session.pop("current_pdf_path", None)
    session.pop("current_pdf_stored_filename", None)
    session.pop("current_stored_filename", None)
    session.pop("current_storage_path", None)
    session.pop("original_filename", None)


def save_pdf_locally(original_filename, pdf_bytes=None, file_storage=None):
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    stored_filename = make_saved_pdf_filename()
    save_path = os.path.join(get_upload_folder(), stored_filename)

    if pdf_bytes is not None:
        with open(save_path, "wb") as pdf_file:
            pdf_file.write(pdf_bytes)
    else:
        file_storage.save(save_path)

    file_index = load_file_index()
    file_index[stored_filename] = {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "storage_path": get_storage_path(stored_filename),
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_file_index(file_index)
    set_current_local_pdf(original_filename, stored_filename, save_path)

    return stored_filename, save_path


def get_original_filename(stored_filename):
    supabase_record = get_supabase_pdf_record(stored_filename)
    if supabase_record:
        return supabase_record.get("original_filename") or stored_filename

    file_info = load_file_index().get(stored_filename, {})

    if not isinstance(file_info, dict):
        return stored_filename

    return file_info.get("original_filename") or stored_filename


def extract_pdf_text(reader):
    text_parts = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_parts.append(page_text.strip())

    text = "\n\n".join(text_parts).strip()
    if not text:
        return None, "PDF \u6ca1\u6709\u8bfb\u53d6\u5230\u6587\u5b57\u5185\u5bb9\uff0c\u53ef\u80fd\u662f\u626b\u63cf\u7248\u6216\u56fe\u7247\u578b PDF\u3002"

    return text, None


def read_pdf_text(file_path):
    if PdfReader is None:
        return None, "\u7f3a\u5c11\u4f9d\u8d56 pypdf\uff0c\u8bf7\u5148\u8fd0\u884c\uff1apip install pypdf"

    try:
        reader = PdfReader(file_path)
    except Exception:
        return None, "PDF \u8bfb\u53d6\u5931\u8d25\uff0c\u8bf7\u786e\u8ba4\u6587\u4ef6\u6ca1\u6709\u635f\u574f\u6216\u52a0\u5bc6\u3002"

    return extract_pdf_text(reader)


def read_pdf_text_from_bytes(pdf_bytes):
    if PdfReader is None:
        return None, "\u7f3a\u5c11\u4f9d\u8d56 pypdf\uff0c\u8bf7\u5148\u8fd0\u884c\uff1apip install pypdf"

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        return None, "PDF \u8bfb\u53d6\u5931\u8d25\uff0c\u8bf7\u786e\u8ba4\u6587\u4ef6\u6ca1\u6709\u635f\u574f\u6216\u52a0\u5bc6\u3002"

    return extract_pdf_text(reader)


def get_upload_folder():
    return os.path.abspath(app.config["UPLOAD_FOLDER"])


def get_storage_path(stored_filename):
    return f"pdfs/{stored_filename}"


def make_user_storage_path(user_id, original_filename):
    safe_name = secure_filename(original_filename or "upload.pdf")
    if not safe_name:
        safe_name = "upload.pdf"
    if not is_pdf(safe_name):
        safe_name = f"{safe_name}.pdf"
    return f"{user_id}/{uuid.uuid4().hex}_{safe_name}"


def is_safe_user_storage_path(storage_path, user_id):
    if not storage_path or not user_id:
        return False
    if not storage_path.startswith(f"{user_id}/"):
        return False
    if storage_path != f"{user_id}/{os.path.basename(storage_path)}":
        return False
    return is_pdf(os.path.basename(storage_path))


def upload_pdf_to_storage(storage_path, pdf_bytes):
    print("[STORAGE] uploading...")
    try:
        supabase_client.storage.from_(SUPABASE_BUCKET).upload(
            storage_path,
            pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "false"},
        )
        print("[STORAGE] upload done")
        return True, None
    except Exception as exc:
        print("[STORAGE] upload failed:", exc)
        return False, str(exc)


def download_pdf_from_storage(storage_path):
    user_id = get_current_user_id()
    old_flat_path = storage_path == f"pdfs/{os.path.basename(storage_path or '')}"
    if not storage_path or not (old_flat_path or is_safe_user_storage_path(storage_path, user_id)):
        return None, "\u5f53\u524d PDF \u5b58\u50a8\u8def\u5f84\u4e0d\u5408\u6cd5\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9 PDF\u3002"

    print("[STORAGE] downloading...")
    try:
        pdf_bytes = supabase_client.storage.from_(SUPABASE_BUCKET).download(storage_path)
        print("[STORAGE] download done")
        return pdf_bytes, None
    except Exception as exc:
        print("[STORAGE] download failed:", exc)
        return None, "\u4ece Supabase Storage \u4e0b\u8f7d PDF \u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002"


def delete_pdf_from_storage(storage_path):
    user_id = get_current_user_id()
    old_flat_path = storage_path == f"pdfs/{os.path.basename(storage_path or '')}"
    if not storage_path or not (old_flat_path or is_safe_user_storage_path(storage_path, user_id)):
        return False, "\u6587\u4ef6\u5b58\u50a8\u8def\u5f84\u4e0d\u5408\u6cd5\uff0c\u65e0\u6cd5\u5220\u9664\u3002"

    print("[STORAGE] deleting...")
    try:
        supabase_client.storage.from_(SUPABASE_BUCKET).remove([storage_path])
        print("[STORAGE] delete done")
        return True, None
    except Exception as exc:
        print("[STORAGE] delete failed:", exc)
        return False, str(exc)


TABLE_CONFIG_ERROR_MESSAGE = "数据库表配置错误，请检查 Supabase 表名。"


def is_supabase_table_missing_error(exc):
    error_text = str(exc).lower()
    return (
        "pgrst205" in error_text
        or "42703" in error_text
        or "could not find the table" in error_text
        or "column" in error_text and "does not exist" in error_text
        or "schema cache" in error_text
    )


def get_document_select_columns():
    return "id, user_id, original_filename, storage_path, summary, visual_summary, created_at"


def get_user_documents():
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return [], None

    try:
        response = (
            supabase_client.table(SUPABASE_TABLE)
            .select(get_document_select_columns())
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        print("[SUPABASE] document list failed:", exc)
        if is_supabase_table_missing_error(exc):
            return [], TABLE_CONFIG_ERROR_MESSAGE
        return [], None

    return response.data or [], None


def get_user_document(document_id):
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return None

    try:
        uuid.UUID(str(document_id))
    except (TypeError, ValueError):
        return None

    try:
        response = (
            supabase_client.table(SUPABASE_TABLE)
            .select(get_document_select_columns())
            .eq("id", str(document_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        print("[SUPABASE] document query failed:", exc)
        if is_supabase_table_missing_error(exc):
            session["database_error_message"] = TABLE_CONFIG_ERROR_MESSAGE
        return None

    if not response.data:
        return None
    return response.data[0]


def insert_user_document(original_filename, storage_path):
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return None, "Supabase is not configured"

    try:
        stored_filename = os.path.basename(storage_path)
        response = (
            supabase_client.table(SUPABASE_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "original_filename": original_filename,
                    "stored_filename": stored_filename,
                    "storage_path": storage_path,
                }
            )
            .execute()
        )
    except Exception as exc:
        print("[SUPABASE] document insert failed:", exc)
        if is_supabase_table_missing_error(exc):
            return None, TABLE_CONFIG_ERROR_MESSAGE
        return None, str(exc)

    if not response.data:
        return None, "No document record returned"
    return response.data[0], None


def update_user_document_summary(document_id, summary):
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return False

    try:
        supabase_client.table(SUPABASE_TABLE).update({"summary": summary}).eq(
            "id", str(document_id)
        ).eq("user_id", user_id).execute()
        return True
    except Exception as exc:
        print("[SUPABASE] summary update failed:", exc)
        return False


def update_user_document_visual_summary(document_id, visual_summary):
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return False

    try:
        supabase_client.table(SUPABASE_TABLE).update(
            {"visual_summary": visual_summary}
        ).eq("id", str(document_id)).eq("user_id", user_id).execute()
        return True
    except Exception as exc:
        print("[SUPABASE] visual summary update failed:", exc)
        return False


def delete_user_document_record(document_id):
    user_id = get_current_user_id()
    if not is_supabase_enabled() or not user_id:
        return False

    try:
        supabase_client.table(SUPABASE_TABLE).delete().eq(
            "id", str(document_id)
        ).eq("user_id", user_id).execute()
        return True
    except Exception as exc:
        print("[SUPABASE] document delete failed:", exc)
        return False


def get_supabase_pdf_record(stored_filename):
    if not is_supabase_enabled() or not is_safe_pdf_filename(stored_filename):
        return None

    try:
        response = (
            supabase_client.table(SUPABASE_TABLE)
            .select("original_filename, stored_filename, storage_path, uploaded_at")
            .eq("stored_filename", stored_filename)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        print(f"Supabase 查询失败，将使用本地文件索引：{exc}")
        return None

    if not response.data:
        return None

    return response.data[0]


def insert_supabase_pdf_record(original_filename, stored_filename, storage_path):
    if not is_supabase_enabled():
        if is_supabase_configured():
            print("[SUPABASE] insert failed:", "client is not enabled")
        return False, "Supabase client is not enabled"

    try:
        print("[SUPABASE] inserting metadata...")
        response = supabase_client.table(SUPABASE_TABLE).insert(
            {
                "original_filename": original_filename,
                "stored_filename": stored_filename,
                "storage_path": storage_path,
            }
        ).execute()
        print("[SUPABASE] insert response:", response)
        return True, None
    except Exception as exc:
        print("[SUPABASE] insert failed:", exc)
        return False, str(exc)


def delete_supabase_pdf_record(stored_filename):
    if not is_supabase_enabled() or not is_safe_pdf_filename(stored_filename):
        return False

    try:
        supabase_client.table(SUPABASE_TABLE).delete().eq(
            "stored_filename", stored_filename
        ).execute()
        return True
    except Exception as exc:
        print(f"Supabase 删除失败，本地文件删除不受影响：{exc}")
        return False


def get_supabase_uploaded_files():
    if not is_supabase_enabled():
        return None

    try:
        response = (
            supabase_client.table(SUPABASE_TABLE)
            .select("original_filename, stored_filename, storage_path, uploaded_at")
            .order("uploaded_at", desc=True)
            .execute()
        )
    except Exception as exc:
        print("[SUPABASE] list failed:", exc)
        return None

    pdf_files = []
    for file_info in response.data or []:
        stored_filename = file_info.get("stored_filename")
        if not is_safe_pdf_filename(stored_filename):
            continue

        original_filename = file_info.get("original_filename") or ""
        pdf_files.append(
            {
                "stored_filename": stored_filename,
                "display_filename": original_filename or stored_filename,
            }
        )

    return pdf_files


def get_local_uploaded_files():
    upload_folder = get_upload_folder()

    if not os.path.isdir(upload_folder):
        return []

    file_index = load_file_index()
    pdf_files = []
    for filename in os.listdir(upload_folder):
        file_path = os.path.join(upload_folder, filename)
        if os.path.isfile(file_path) and is_pdf(filename):
            file_info = file_index.get(filename, {})
            original_filename = ""

            if isinstance(file_info, dict):
                original_filename = file_info.get("original_filename", "")

            pdf_files.append(
                {
                    "stored_filename": filename,
                    "display_filename": original_filename or filename,
                }
            )

    return sorted(pdf_files, key=lambda item: item["display_filename"].lower())


def get_uploaded_files():
    local_files = get_local_uploaded_files()
    supabase_files = get_supabase_uploaded_files()

    if supabase_files is None:
        return local_files

    merged_files = {file_info["stored_filename"]: file_info for file_info in local_files}
    for file_info in supabase_files:
        merged_files[file_info["stored_filename"]] = file_info

    return sorted(
        merged_files.values(),
        key=lambda item: item["display_filename"].lower(),
    )


def is_safe_pdf_filename(filename):
    if not filename or filename != os.path.basename(filename) or not is_pdf(filename):
        return False

    return True


def get_upload_pdf_path(filename, must_exist=True):
    if not is_safe_pdf_filename(filename):
        return None

    upload_folder = get_upload_folder()
    file_path = os.path.abspath(os.path.join(upload_folder, filename))

    if os.path.commonpath([upload_folder, file_path]) != upload_folder:
        return None

    if must_exist and not os.path.isfile(file_path):
        return None

    return file_path


def get_safe_pdf_path(filename):
    return get_upload_pdf_path(filename, must_exist=True)


def call_dashscope(prompt):
    if OpenAI is None:
        return "\u7f3a\u5c11\u4f9d\u8d56 openai\uff0c\u8bf7\u5148\u8fd0\u884c\uff1apip install openai"

    api_key = os.getenv("DASHSCOPE_API_KEY")

    if not api_key or api_key.startswith("\u8bf7\u5728\u8fd9\u91cc"):
        return "\u8bf7\u5148\u914d\u7f6e\u963f\u91cc\u4e91\u767e\u70bc API Key\u3002"

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    )
    model = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "\u4f60\u662f\u4e00\u4f4d\u64c5\u957f\u4e2d\u6587\u8bb2\u89e3\u7684 PDF \u5b66\u4e60\u52a9\u624b\u3002",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        return "\u963f\u91cc\u4e91\u767e\u70bc\u5927\u6a21\u578b\u8c03\u7528\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5 API Key\u3001\u6a21\u578b\u540d\u79f0\u6216\u7f51\u7edc\u8fde\u63a5\u3002"


def make_image_data_url(image_bytes, mime_type):
    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded_image}"


def get_image_mime_type(image_ext):
    ext = (image_ext or "png").lower()
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "webp":
        return "image/webp"
    return "image/png"


def extract_pdf_visual_items(pdf_bytes):
    try:
        import fitz
    except ImportError:
        return [], "当前环境缺少 PyMuPDF，无法进行图表识别。请先安装依赖或在线上环境测试。"

    max_images = int(os.getenv("PDF_VISUAL_MAX_IMAGES", "4"))
    max_pages = int(os.getenv("PDF_VISUAL_MAX_PAGES", "3"))
    visual_items = []

    try:
        pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return [], "PDF 图像解析失败，请确认文件没有损坏或加密。"

    try:
        seen_xrefs = set()
        for page_index in range(min(len(pdf_document), max_pages)):
            page = pdf_document[page_index]
            for image_info in page.get_images(full=True):
                if len(visual_items) >= max_images:
                    break

                xref = image_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    extracted = pdf_document.extract_image(xref)
                except Exception:
                    continue

                image_bytes = extracted.get("image")
                if not image_bytes or len(image_bytes) < 4096:
                    continue

                image_ext = extracted.get("ext", "png")
                visual_items.append(
                    {
                        "label": f"第 {page_index + 1} 页内嵌图片",
                        "bytes": image_bytes,
                        "mime_type": get_image_mime_type(image_ext),
                    }
                )

        for page_index in range(min(len(pdf_document), max_pages)):
            if len(visual_items) >= max_images + max_pages:
                break

            try:
                page = pdf_document[page_index]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                visual_items.append(
                    {
                        "label": f"第 {page_index + 1} 页页面截图",
                        "bytes": pixmap.tobytes("png"),
                        "mime_type": "image/png",
                    }
                )
            except Exception:
                continue
    finally:
        pdf_document.close()

    if not visual_items:
        return [], "没有从 PDF 中提取到可用于图表识别的图片或页面截图。"

    return visual_items, None


def call_dashscope_vision(prompt, visual_items):
    if OpenAI is None:
        return "缺少依赖 openai，请先运行：pip install openai"

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key or api_key.startswith("\u8bf7\u5728\u8fd9\u91cc"):
        return "请先配置阿里云百炼 API Key。"

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    )
    model = os.getenv("DASHSCOPE_VISION_MODEL", "qwen-vl-plus")

    content = [{"type": "text", "text": prompt}]
    for item in visual_items:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": make_image_data_url(item["bytes"], item["mime_type"])
                },
            }
        )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一位擅长学术论文图表解读的中文研究助理。",
                },
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        print("[DASHSCOPE] vision call failed:", exc)
        return "图表识别调用失败，请检查视觉模型名称、API Key 或网络连接。"


def generate_visual_summary(pdf_bytes, pdf_text=""):
    visual_items, error = extract_pdf_visual_items(pdf_bytes)
    if error:
        return error

    item_labels = "\n".join(
        f"{index}. {item['label']}" for index, item in enumerate(visual_items, start=1)
    )
    text_context = (pdf_text or "")[:2500]
    prompt = f"""
请识别下面这些来自同一篇 PDF 论文的页面截图/内嵌图片，重点找出关键图表、表格、流程图、模型结构图、实验结果图。

图片来源：
{item_labels}

请用中文输出，严格包含以下部分：
## 关键图表清单
逐条说明每张关键图表可能对应的 Figure/Table、展示对象、坐标轴/列名/模块名称、主要趋势或结论。

## 图表支持的核心论点
总结这些图表如何支持论文的主要观点、方法或实验结论。

## 可用于问答的图表事实
列出可以被后续问答引用的具体事实。无法确定的内容请标注“不确定”，不要编造数字。

可参考的论文文字片段：
{text_context}
"""
    return call_dashscope_vision(prompt, visual_items)


def build_visual_context(visual_summary):
    if not visual_summary:
        return "暂无图表识别结果。"
    return visual_summary[:4000]


def generate_summary(pdf_text, visual_summary=""):
    text_for_summary = pdf_text[:6000]
    visual_context = build_visual_context(visual_summary)
    prompt = f"""
\u8bf7\u6839\u636e\u4e0b\u9762\u7684 PDF \u6587\u672c\u751f\u6210\u4e00\u4efd\u9002\u5408\u5b66\u4e60\u8005\u9605\u8bfb\u7684\u7ed3\u6784\u5316\u4e2d\u6587\u5b66\u4e60\u6458\u8981\u3002

\u8981\u6c42\uff1a
1. \u5148\u5224\u65ad\u6587\u6863\u8bed\u8a00\uff0c\u9700\u8981\u652f\u6301\u4e2d\u6587\u3001\u82f1\u6587\u548c\u5fb7\u8bed\u3002
2. \u5982\u679c\u68c0\u6d4b\u5230\u5fb7\u8bed\u5185\u5bb9\uff0c\u8bf7\u7528\u4e2d\u6587\u89e3\u91ca\u6587\u6863\u5185\u5bb9\uff0c\u5e76\u4fdd\u7559\u91cd\u8981\u5fb7\u8bed\u8bcd\u6c47\u3002
3. \u4e0d\u8981\u7f16\u9020 PDF \u91cc\u6ca1\u6709\u7684\u4fe1\u606f\u3002
4. 如果提供了图表识别结果，请把关键图表、表格、模型结构图和实验结果纳入总结。
5. \u8bf7\u4e25\u683c\u6309\u4ee5\u4e0b 6 \u4e2a\u6807\u9898\u8f93\u51fa\uff1a

## \u6587\u6863\u8bed\u8a00\u5224\u65ad
## \u4e2d\u6587\u6458\u8981
## \u91cd\u70b9\u5185\u5bb9
## 关键图表与实验结果
## \u5173\u952e\u8bcd / \u751f\u8bcd\u89e3\u91ca
## \u590d\u4e60\u5efa\u8bae

PDF \u6587\u672c\uff1a
{text_for_summary}

图表识别结果：
{visual_context}
"""
    return call_dashscope(prompt)


def get_question_keywords(question):
    # 简化版 RAG：不用向量数据库，提取问题关键词，并做多语言关键词扩展。
    # 例如中文问“到达/交通攻略”，也会匹配英文、法语、德语 PDF 里的 access/entrée/Anfahrt 等词。
    stop_words = {
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "how",
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "ist",
        "sind",
        "der",
        "die",
        "das",
        "und",
        "oder",
        "was",
        "wie",
        "wo",
        "wann",
        "请问",
        "什么",
        "怎么",
        "如何",
        "为什么",
        "哪些",
        "这个",
        "文档",
        "内容",
    }
    words = re.findall(r"[\w\u4e00-\u9fff]+", question.lower())
    keywords = []

    for word in words:
        if len(word) <= 1 or word in stop_words:
            continue

        keywords.append(word)

        if re.search(r"[\u4e00-\u9fff]", word) and len(word) > 2:
            for index in range(len(word) - 1):
                chinese_pair = word[index : index + 2]
                if chinese_pair not in stop_words:
                    keywords.append(chinese_pair)

    lower_question = question.lower()
    multilingual_groups = [
        {
            "triggers": [
                "\u4ea4\u901a",
                "\u5230\u8fbe",
                "\u8def\u7ebf",
                "\u600e\u4e48\u53bb",
                "\u600e\u6837\u53bb",
                "\u5982\u4f55\u53bb",
                "\u5730\u94c1",
                "\u516c\u4ea4",
                "\u5165\u53e3",
                "\u8fdb\u5165",
                "\u535a\u7269\u9986",
                "getting to",
                "access",
                "entrance",
                "metro",
                "bus",
                "museum",
                "acc\u00e8s",
                "entr\u00e9e",
                "m\u00e9tro",
                "mus\u00e9e",
                "venir",
                "anfahrt",
                "zugang",
                "eingang",
                "u-bahn",
            ],
            "expanded": [
                "\u4ea4\u901a",
                "\u5230\u8fbe",
                "\u8def\u7ebf",
                "\u5730\u94c1",
                "\u516c\u4ea4",
                "\u5165\u53e3",
                "\u535a\u7269\u9986",
                "getting to",
                "access",
                "entrance",
                "metro",
                "bus",
                "museum",
                "acc\u00e8s",
                "entree",
                "entr\u00e9e",
                "m\u00e9tro",
                "metro",
                "mus\u00e9e",
                "musee",
                "venir",
                "anfahrt",
                "zugang",
                "eingang",
                "u-bahn",
                "ubahn",
            ],
        }
    ]

    for group in multilingual_groups:
        if any(trigger in lower_question for trigger in group["triggers"]):
            keywords.extend(group["expanded"])

    return list(dict.fromkeys(keywords))


def split_text_into_chunks(text, chunk_size=900, overlap=120):
    # 简化版 RAG：先按段落切分；段落太长时，再按固定长度切成小块。
    chunks = []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]

    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            chunks.append(paragraph)
            continue

        start = 0
        while start < len(paragraph):
            chunk = paragraph[start : start + chunk_size].strip()
            if chunk:
                chunks.append(chunk)
            start += chunk_size - overlap

    return chunks


def find_relevant_chunks(pdf_text, question, max_chunks=5):
    # 简化版 RAG：根据问题关键词出现次数给每个 chunk 打分，取最相关的 3-5 个片段。
    chunks = split_text_into_chunks(pdf_text)
    keywords = get_question_keywords(question)

    if not chunks or not keywords:
        return []

    scored_chunks = []
    for index, chunk in enumerate(chunks):
        lower_chunk = chunk.lower()
        score = sum(lower_chunk.count(keyword) for keyword in keywords)
        if score > 0:
            scored_chunks.append((score, index, chunk))

    scored_chunks.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for score, index, chunk in scored_chunks[:max_chunks]]


def is_learning_advice_question(question):
    advice_keywords = [
        "\u5b66\u4e60",
        "\u590d\u4e60",
        "\u8ba1\u5212",
        "\u65f6\u95f4",
        "\u591a\u4e45",
        "\u51e0\u5c0f\u65f6",
        "\u51e0\u5206\u949f",
        "\u9700\u8981\u591a\u4e45",
        "\u51c6\u5907",
        "\u8003\u8bd5",
        "\u91cd\u70b9",
        "\u96be\u5ea6",
        "\u5efa\u8bae",
        "\u80cc\u8bf5",
        "\u6d4f\u89c8",
        "\u638c\u63e1",
        "study",
        "review",
        "plan",
        "time",
        "how long",
        "exam",
        "prepare",
        "difficulty",
        "lernen",
        "wiederholen",
        "pr\u00fcfung",
        "zeit",
        "dauer",
    ]
    lower_question = question.lower()
    return any(keyword in lower_question for keyword in advice_keywords)


def is_summary_or_guide_question(question):
    guide_keywords = [
        "\u603b\u7ed3",
        "\u6982\u62ec",
        "\u6574\u7406",
        "\u653b\u7565",
        "\u5efa\u8bae",
        "\u600e\u4e48\u53bb",
        "\u5230\u8fbe",
        "\u4ea4\u901a",
        "\u8def\u7ebf",
        "\u5165\u53e3",
        "\u5730\u94c1",
        "\u516c\u4ea4",
        "summarize",
        "summary",
        "guide",
        "getting to",
        "access",
        "entrance",
    ]
    lower_question = question.lower()
    return any(keyword in lower_question for keyword in guide_keywords)


def answer_question(pdf_text, question, visual_summary=""):
    is_advice_question = is_learning_advice_question(question)
    is_guide_question = is_summary_or_guide_question(question)
    relevant_chunks = find_relevant_chunks(pdf_text, question)
    used_fallback_context = False

    if not relevant_chunks and is_advice_question:
        relevant_chunks = split_text_into_chunks(pdf_text)[:5]

    if not relevant_chunks:
        # 多语言关键词扩展 + fallback 检索机制：
        # 如果关键词没有命中 chunk，不直接判定失败，而是把 PDF 前 6000 字交给模型再次判断。
        relevant_chunks = [pdf_text[:6000]]
        used_fallback_context = True

    context_parts = []
    for index, chunk in enumerate(relevant_chunks, start=1):
        context_parts.append(f"\u7247\u6bb5 {index}\uff1a\n{chunk}")

    rag_context = "\n\n".join(context_parts)
    if is_advice_question:
        question_type = "\u5b66\u4e60\u5efa\u8bae/\u65f6\u95f4\u4f30\u7b97\u7c7b"
    elif is_guide_question:
        question_type = "\u603b\u7ed3/\u653b\u7565/\u5efa\u8bae\u7c7b"
    else:
        question_type = "\u6587\u6863\u4e8b\u5b9e\u68c0\u7d22\u7c7b"

    document_length = len(pdf_text)
    context_source = "\u524d 6000 \u5b57 fallback \u4e0a\u4e0b\u6587" if used_fallback_context else "\u5173\u952e\u8bcd\u68c0\u7d22\u5230\u7684\u76f8\u5173\u7247\u6bb5"
    visual_context = build_visual_context(visual_summary)
    prompt = f"""
\u8bf7\u6839\u636e\u4e0b\u9762\u7684\u201c\u53c2\u8003\u4e0a\u4e0b\u6587\u201d\u56de\u7b54\u7528\u6237\u95ee\u9898\u3002

\u95ee\u9898\u7c7b\u578b\uff1a{question_type}
\u6587\u6863\u603b\u5b57\u7b26\u6570\uff08\u7c97\u7565\uff09\uff1a{document_length}
\u672c\u6b21\u4f7f\u7528\u7684\u53c2\u8003\u7247\u6bb5\u6570\uff1a{len(relevant_chunks)}
\u4e0a\u4e0b\u6587\u6765\u6e90\uff1a{context_source}
\u56fe\u8868\u8bc6\u522b\u7ed3\u679c\uff1a{visual_context}

\u8981\u6c42\uff1a
1. \u4f18\u5148\u7528\u4e2d\u6587\u56de\u7b54\uff0c\u5fc5\u8981\u65f6\u4fdd\u7559\u6587\u6863\u4e2d\u91cd\u8981\u7684\u5fb7\u8bed/\u82f1\u8bed/\u6cd5\u8bed\u5173\u952e\u8bcd\u3002
2. \u5982\u679c\u7528\u6237\u95ee\u7684\u662f\u6587\u6863\u4e8b\u5b9e\uff08\u4eba\u7269\u3001\u65e5\u671f\u3001\u5730\u70b9\u3001\u5b9a\u4e49\u3001\u6d41\u7a0b\u3001\u6761\u6b3e\u7b49\uff09\uff0c\u5fc5\u987b\u4e25\u683c\u57fa\u4e8e\u53c2\u8003\u4e0a\u4e0b\u6587\u56de\u7b54\u3002
3. \u5982\u679c\u6587\u6863\u4e8b\u5b9e\u95ee\u9898\u5728\u53c2\u8003\u4e0a\u4e0b\u6587\u4e2d\u6ca1\u6709\u76f8\u5173\u4fe1\u606f\uff0c\u8bf7\u56de\u7b54\uff1a\u201c\u6587\u6863\u4e2d\u6ca1\u6709\u627e\u5230\u660e\u786e\u7b54\u6848\u3002\u201d
4. \u5982\u679c\u7528\u6237\u95ee\u7684\u662f\u603b\u7ed3\u3001\u653b\u7565\u3001\u4ea4\u901a\u8def\u7ebf\u3001\u5230\u8fbe\u65b9\u5f0f\u3001\u5b66\u4e60\u5efa\u8bae\u3001\u590d\u4e60\u8ba1\u5212\u3001\u65f6\u95f4\u4f30\u7b97\u3001\u91cd\u70b9\u63d0\u70bc\u3001\u96be\u5ea6\u5224\u65ad\u3001\u5982\u4f55\u51c6\u5907\u8003\u8bd5\u7b49\u95ee\u9898\uff0c\u53ef\u4ee5\u57fa\u4e8e\u53c2\u8003\u4e0a\u4e0b\u6587\u6574\u7406\u6210\u6e05\u6670\u6b65\u9aa4\u6216\u5efa\u8bae\u3002
5. \u5bf9\u4e8e\u5b66\u4e60\u5efa\u8bae/\u4f30\u7b97\u7c7b\u95ee\u9898\uff0c\u56de\u7b54\u5f00\u5934\u5fc5\u987b\u5199\uff1a\u201c\u4ee5\u4e0b\u662f\u57fa\u4e8e\u6587\u6863\u5185\u5bb9\u7684\u4f30\u7b97/\u5efa\u8bae\u3002\u201d
6. \u5982\u679c\u7528\u6237\u95ee\u201c\u9700\u8981\u591a\u4e45\u590d\u4e60\u5b8c\u201d\u8fd9\u7c7b\u95ee\u9898\uff0c\u8bf7\u53c2\u8003\u6587\u6863\u5b57\u6570/\u957f\u5ea6\u3001\u77e5\u8bc6\u70b9\u6570\u91cf\u3001\u662f\u5426\u9700\u8981\u80cc\u8bf5\u3001\u662f\u5426\u53ea\u662f\u5feb\u901f\u6d4f\u89c8\u3001\u662f\u5426\u8981\u51c6\u5907\u8003\u8bd5\uff0c\u5e76\u7ed9\u51fa\u5206\u6863\u5efa\u8bae\uff1a\u5feb\u901f\u6d4f\u89c8\u3001\u7406\u89e3\u590d\u4e60\u3001\u8003\u524d\u80cc\u8bf5/\u505a\u9898\u3002
7. \u5982\u679c\u7528\u6237\u95ee\u201c\u4ea4\u901a\u653b\u7565/\u5230\u8fbe\u65b9\u5f0f\u201d\uff0c\u8bf7\u4f18\u5148\u67e5\u627e GETTING TO THE MUSEUM\u3001access\u3001entrance\u3001metro\u3001bus\u3001acc\u00e8s\u3001entr\u00e9e\u3001m\u00e9tro\u3001Anfahrt\u3001Zugang\u3001Eingang \u7b49\u76f8\u5173\u5185\u5bb9\uff0c\u5e76\u6574\u7406\u6210\u4e2d\u6587\u8981\u70b9\u3002
8. \u4e0d\u8981\u7f16\u9020 PDF \u91cc\u6ca1\u6709\u7684\u4e8b\u5b9e\uff1b\u4f30\u7b97\u548c\u5efa\u8bae\u8981\u660e\u786e\u6807\u6ce8\u4e3a\u4f30\u7b97/\u5efa\u8bae\u3002
9. 如果问题涉及图表、实验结果、模型结构、趋势、数值或表格，请优先结合“图表识别结果”回答；不确定的图表细节必须说明不确定。
10. \u56de\u7b54\u4e2d\u53ef\u4ee5\u7b80\u77ed\u8bf4\u660e\uff1a\u672c\u6b21\u4f7f\u7528\u4e86 {len(relevant_chunks)} \u4e2a\u53c2\u8003\u7247\u6bb5\u3002

\u7528\u6237\u95ee\u9898\uff1a
{question}

\u53c2\u8003\u4e0a\u4e0b\u6587\uff1a
{rag_context}
"""
    return call_dashscope(prompt)


def get_current_pdf_text():
    document_id = session.get("current_document_id")
    if document_id:
        document = get_user_document(document_id)
        if not document:
            return None, pop_database_error_message(
                "当前 PDF 不存在或不属于当前登录用户，请重新选择。"
            )
        pdf_bytes, error = download_pdf_from_storage(document.get("storage_path"))
        if error:
            return None, error
        return read_pdf_text_from_bytes(pdf_bytes)

    stored_filename = session.get("current_pdf_stored_filename")
    local_file_path = get_safe_pdf_path(stored_filename) if stored_filename else None

    if local_file_path:
        session["current_pdf_path"] = local_file_path
        return read_pdf_text(local_file_path)

    if is_supabase_enabled():
        storage_path = session.get("current_storage_path")

        if not storage_path:
            return None, "\u8bf7\u5148\u4e0a\u4f20\u6216\u9009\u62e9 PDF \u6587\u4ef6\u3002"

        pdf_bytes, error = download_pdf_from_storage(storage_path)
        if error:
            return None, error

        return read_pdf_text_from_bytes(pdf_bytes)

    file_path = get_safe_pdf_path(stored_filename) if stored_filename else session.get("current_pdf_path")

    if not file_path:
        return None, "\u8bf7\u5148\u4e0a\u4f20\u6216\u9009\u62e9 PDF \u6587\u4ef6\u3002"

    if not is_pdf(os.path.basename(file_path)):
        return None, "\u5f53\u524d\u6587\u4ef6\u4e0d\u662f\u6709\u6548\u7684 PDF\uff0c\u8bf7\u91cd\u65b0\u4e0a\u4f20\u6216\u9009\u62e9 PDF\u3002"

    upload_folder = get_upload_folder()
    safe_file_path = os.path.abspath(file_path)
    if os.path.commonpath([upload_folder, safe_file_path]) != upload_folder:
        return None, "\u5f53\u524d PDF \u8def\u5f84\u4e0d\u5408\u6cd5\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9 PDF\u3002"

    if not os.path.exists(safe_file_path):
        return None, "\u627e\u4e0d\u5230\u5df2\u4e0a\u4f20\u7684 PDF \u6587\u4ef6\uff0c\u8bf7\u91cd\u65b0\u4e0a\u4f20\u3002"

    session["current_pdf_path"] = safe_file_path
    return read_pdf_text(safe_file_path)


def get_current_visual_summary():
    document_id = session.get("current_document_id")
    if not document_id:
        return ""

    document = get_user_document(document_id)
    if not document:
        return ""

    return document.get("visual_summary") or ""


def render_result_html(result):
    if not result:
        return ""

    # \u8fd9\u662f\u672c\u5730\u8bfe\u7a0b\u9879\u76ee\u7684\u7b80\u5355\u6e32\u67d3\uff1a\u5148\u8f6c\u4e49 HTML\uff0c\u518d\u5c06 Markdown \u8f6c\u6210 HTML\u3002
    # \u8fd9\u6837\u53ef\u4ee5\u663e\u793a\u6807\u9898\u3001\u52a0\u7c97\u548c\u5217\u8868\uff0c\u540c\u65f6\u907f\u514d\u76f4\u63a5\u6e32\u67d3\u6a21\u578b\u8fd4\u56de\u7684 HTML\u3002
    safe_text = escape(result)
    if markdown is None:
        return safe_text.replace("\n", "<br>")

    return markdown.markdown(safe_text, extensions=["extra"])


def render_home(upload_message="", result="", status_code=200):
    documents, documents_error = get_user_documents()
    if documents_error and not upload_message:
        upload_message = documents_error
    current_document_id = session.get("current_document_id")
    current_document = None

    for document in documents:
        if document.get("id") == current_document_id:
            current_document = document
            break

    if not current_document and documents:
        current_document = documents[0]
        session["current_document_id"] = current_document.get("id")
        session["current_pdf_name"] = current_document.get("original_filename")

    if current_document and not upload_message:
        upload_message = f"当前 PDF：{current_document.get('original_filename')}"

    if not result and current_document and current_document.get("summary"):
        result = current_document.get("summary")

    return (
        render_template(
            "index.html",
            result_html=render_result_html(result),
            visual_summary_html=render_result_html(
                current_document.get("visual_summary") if current_document else ""
            ),
            upload_message=upload_message,
            documents=documents,
            current_document=current_document,
            current_document_id=session.get("current_document_id"),
            user_email=get_current_user_email(),
        ),
        status_code,
    )


def get_auth_error_message(exc, default_message):
    error_text = str(exc).lower()

    if (
        "timeout" in error_text
        or "timed out" in error_text
        or "read operation timed out" in error_text
    ):
        return "连接 Supabase 超时，请检查网络、代理或稍后重试。"
    if "invalid login credentials" in error_text:
        return "邮箱或密码不正确。"
    if "email not confirmed" in error_text:
        return "该邮箱还未完成验证。"

    return default_message


def pop_database_error_message(default_message):
    return session.pop("database_error_message", None) or default_message


@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user_id():
        return redirect(url_for("index"))

    message = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            message = "请输入邮箱和密码。"
        elif len(password) < 6:
            message = "密码至少需要 6 位。"
        elif not is_auth_enabled():
            message = "Supabase Auth 未配置，请检查 SUPABASE_ANON_KEY。"
        else:
            try:
                supabase_auth_client.auth.sign_up(
                    {"email": email, "password": password}
                )
                return render_template(
                    "login.html",
                    message="注册成功，请登录。",
                    email=email,
                )
            except Exception as exc:
                print("[AUTH] register failed:", exc)
                message = get_auth_error_message(
                    exc,
                    "注册失败，请确认邮箱格式、密码强度，或稍后重试。",
                )

    return render_template("register.html", message=message)


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user_id():
        return redirect(url_for("index"))

    message = ""
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            message = "请输入邮箱和密码。"
        elif not is_auth_enabled():
            message = "Supabase Auth 未配置，请检查 SUPABASE_ANON_KEY。"
        else:
            try:
                auth_response = supabase_auth_client.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                if not auth_response.user or not auth_response.session:
                    message = "登录失败，请检查邮箱和密码。"
                else:
                    session.clear()
                    session["user_id"] = auth_response.user.id
                    session["user_email"] = auth_response.user.email
                    session["access_token"] = auth_response.session.access_token
                    session["refresh_token"] = auth_response.session.refresh_token
                    return redirect(url_for("index"))
            except Exception as exc:
                print("[AUTH] login failed:", exc)
                message = get_auth_error_message(
                    exc,
                    "登录失败，请检查邮箱和密码。",
                )

    return render_template("login.html", message=message, email=email)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "ask":
            question = request.form.get("question", "").strip()
            if not question:
                return render_home(result="请输入你的问题。")

            pdf_text, error = get_current_pdf_text()
            if error:
                return render_home(result=error)

            return render_home(
                result=answer_question(
                    pdf_text,
                    question,
                    get_current_visual_summary(),
                )
            )

    return render_home()


@app.route("/files")
@login_required
def files():
    return render_home()


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if not is_supabase_enabled():
        return render_home(upload_message="Supabase 未配置，无法上传 PDF。", status_code=503)

    pdf_file = request.files.get("pdf")
    if not pdf_file or pdf_file.filename == "":
        return render_home(upload_message="请先选择 PDF 文件。", status_code=400)
    if not is_allowed_pdf_upload(pdf_file):
        return render_home(upload_message="只支持上传 PDF 文件。", status_code=400)

    original_filename = pdf_file.filename.replace("\\", "/").split("/")[-1]
    safe_name = secure_filename(original_filename)
    if not safe_name:
        return render_home(upload_message="文件名不合法，请重命名后再上传。", status_code=400)

    pdf_bytes = pdf_file.read()
    if not pdf_bytes:
        return render_home(upload_message="PDF 文件为空。", status_code=400)

    storage_path = make_user_storage_path(get_current_user_id(), original_filename)
    storage_saved, storage_error = upload_pdf_to_storage(storage_path, pdf_bytes)
    if not storage_saved:
        return render_home(
            upload_message=f"Storage 上传失败：{storage_error}",
            status_code=502,
        )

    document, document_error = insert_user_document(original_filename, storage_path)
    if document_error:
        delete_pdf_from_storage(storage_path)
        return render_home(
            upload_message=f"文档记录写入失败：{document_error}",
            status_code=502,
        )

    session["current_document_id"] = document.get("id")
    session["current_pdf_name"] = original_filename
    return render_home(upload_message=f"PDF 上传成功：{original_filename}")


@app.route("/select/<document_id>", methods=["POST"])
@login_required
def select_document(document_id):
    document = get_user_document(document_id)
    if not document:
        return render_home(
            upload_message=pop_database_error_message("文件不存在或不属于当前用户。"),
            status_code=404,
        )

    session["current_document_id"] = document.get("id")
    session["current_pdf_name"] = document.get("original_filename")
    return render_home(upload_message=f"当前使用文档：{document.get('original_filename')}")


@app.route("/summarize/<document_id>", methods=["POST"])
@login_required
def summarize_document(document_id):
    document = get_user_document(document_id)
    if not document:
        return render_home(
            result=pop_database_error_message("文件不存在或不属于当前用户。"),
            status_code=404,
        )

    session["current_document_id"] = document.get("id")
    session["current_pdf_name"] = document.get("original_filename")
    pdf_bytes, error = download_pdf_from_storage(document.get("storage_path"))
    if error:
        return render_home(result=error, status_code=502)

    pdf_text, error = read_pdf_text_from_bytes(pdf_bytes)
    if error:
        return render_home(result=error, status_code=400)

    summary = generate_summary(pdf_text, document.get("visual_summary") or "")
    update_user_document_summary(document.get("id"), summary)
    return render_home(result=summary)


@app.route("/analyze-figures/<document_id>", methods=["POST"])
@login_required
def analyze_figures_document(document_id):
    document = get_user_document(document_id)
    if not document:
        return render_home(
            result=pop_database_error_message("文件不存在或不属于当前用户。"),
            status_code=404,
        )

    session["current_document_id"] = document.get("id")
    session["current_pdf_name"] = document.get("original_filename")
    pdf_bytes, error = download_pdf_from_storage(document.get("storage_path"))
    if error:
        return render_home(result=error, status_code=502)

    pdf_text, text_error = read_pdf_text_from_bytes(pdf_bytes)
    if text_error:
        pdf_text = ""

    visual_summary = generate_visual_summary(pdf_bytes, pdf_text)
    update_user_document_visual_summary(document.get("id"), visual_summary)
    return render_home(result=visual_summary)


@app.route("/delete/<document_id>", methods=["POST"])
@login_required
def delete_document(document_id):
    document = get_user_document(document_id)
    if not document:
        return render_home(
            upload_message=pop_database_error_message("文件不存在或不属于当前用户。"),
            status_code=404,
        )

    storage_deleted, storage_error = delete_pdf_from_storage(document.get("storage_path"))
    if not storage_deleted:
        return render_home(upload_message=f"Storage 文件删除失败：{storage_error}", status_code=502)

    delete_user_document_record(document.get("id"))
    if session.get("current_document_id") == document.get("id"):
        clear_pdf_session()
    return render_home(upload_message=f"已删除文件：{document.get('original_filename')}")


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.run(debug=True)
