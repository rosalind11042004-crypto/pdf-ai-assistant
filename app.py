import json
import os
import re
import uuid
from datetime import datetime
from html import escape

from flask import Flask, render_template, request, session

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


app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
app.secret_key = "pdf-ai-assistant-demo"

if load_dotenv:
    load_dotenv()


def is_pdf(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


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


def get_original_filename(stored_filename):
    file_info = load_file_index().get(stored_filename, {})

    if not isinstance(file_info, dict):
        return stored_filename

    return file_info.get("original_filename") or stored_filename


def read_pdf_text(file_path):
    if PdfReader is None:
        return None, "\u7f3a\u5c11\u4f9d\u8d56 pypdf\uff0c\u8bf7\u5148\u8fd0\u884c\uff1apip install pypdf"

    try:
        reader = PdfReader(file_path)
        text_parts = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_parts.append(page_text.strip())

        text = "\n\n".join(text_parts).strip()
    except Exception:
        return None, "PDF \u8bfb\u53d6\u5931\u8d25\uff0c\u8bf7\u786e\u8ba4\u6587\u4ef6\u6ca1\u6709\u635f\u574f\u6216\u52a0\u5bc6\u3002"

    if not text:
        return None, "PDF \u6ca1\u6709\u8bfb\u53d6\u5230\u6587\u5b57\u5185\u5bb9\uff0c\u53ef\u80fd\u662f\u626b\u63cf\u7248\u6216\u56fe\u7247\u578b PDF\u3002"

    return text, None


def get_upload_folder():
    return os.path.abspath(app.config["UPLOAD_FOLDER"])


def get_uploaded_files():
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


def get_safe_pdf_path(filename):
    if not filename or filename != os.path.basename(filename) or not is_pdf(filename):
        return None

    upload_folder = get_upload_folder()
    file_path = os.path.abspath(os.path.join(upload_folder, filename))

    if os.path.commonpath([upload_folder, file_path]) != upload_folder:
        return None

    if not os.path.isfile(file_path):
        return None

    return file_path


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


def generate_summary(pdf_text):
    text_for_summary = pdf_text[:6000]
    prompt = f"""
\u8bf7\u6839\u636e\u4e0b\u9762\u7684 PDF \u6587\u672c\u751f\u6210\u4e00\u4efd\u9002\u5408\u5b66\u4e60\u8005\u9605\u8bfb\u7684\u7ed3\u6784\u5316\u4e2d\u6587\u5b66\u4e60\u6458\u8981\u3002

\u8981\u6c42\uff1a
1. \u5148\u5224\u65ad\u6587\u6863\u8bed\u8a00\uff0c\u9700\u8981\u652f\u6301\u4e2d\u6587\u3001\u82f1\u6587\u548c\u5fb7\u8bed\u3002
2. \u5982\u679c\u68c0\u6d4b\u5230\u5fb7\u8bed\u5185\u5bb9\uff0c\u8bf7\u7528\u4e2d\u6587\u89e3\u91ca\u6587\u6863\u5185\u5bb9\uff0c\u5e76\u4fdd\u7559\u91cd\u8981\u5fb7\u8bed\u8bcd\u6c47\u3002
3. \u4e0d\u8981\u7f16\u9020 PDF \u91cc\u6ca1\u6709\u7684\u4fe1\u606f\u3002
4. \u8bf7\u4e25\u683c\u6309\u4ee5\u4e0b 5 \u4e2a\u6807\u9898\u8f93\u51fa\uff1a

## \u6587\u6863\u8bed\u8a00\u5224\u65ad
## \u4e2d\u6587\u6458\u8981
## \u91cd\u70b9\u5185\u5bb9
## \u5173\u952e\u8bcd / \u751f\u8bcd\u89e3\u91ca
## \u590d\u4e60\u5efa\u8bae

PDF \u6587\u672c\uff1a
{text_for_summary}
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


def answer_question(pdf_text, question):
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
    prompt = f"""
\u8bf7\u6839\u636e\u4e0b\u9762\u7684\u201c\u53c2\u8003\u4e0a\u4e0b\u6587\u201d\u56de\u7b54\u7528\u6237\u95ee\u9898\u3002

\u95ee\u9898\u7c7b\u578b\uff1a{question_type}
\u6587\u6863\u603b\u5b57\u7b26\u6570\uff08\u7c97\u7565\uff09\uff1a{document_length}
\u672c\u6b21\u4f7f\u7528\u7684\u53c2\u8003\u7247\u6bb5\u6570\uff1a{len(relevant_chunks)}
\u4e0a\u4e0b\u6587\u6765\u6e90\uff1a{context_source}

\u8981\u6c42\uff1a
1. \u4f18\u5148\u7528\u4e2d\u6587\u56de\u7b54\uff0c\u5fc5\u8981\u65f6\u4fdd\u7559\u6587\u6863\u4e2d\u91cd\u8981\u7684\u5fb7\u8bed/\u82f1\u8bed/\u6cd5\u8bed\u5173\u952e\u8bcd\u3002
2. \u5982\u679c\u7528\u6237\u95ee\u7684\u662f\u6587\u6863\u4e8b\u5b9e\uff08\u4eba\u7269\u3001\u65e5\u671f\u3001\u5730\u70b9\u3001\u5b9a\u4e49\u3001\u6d41\u7a0b\u3001\u6761\u6b3e\u7b49\uff09\uff0c\u5fc5\u987b\u4e25\u683c\u57fa\u4e8e\u53c2\u8003\u4e0a\u4e0b\u6587\u56de\u7b54\u3002
3. \u5982\u679c\u6587\u6863\u4e8b\u5b9e\u95ee\u9898\u5728\u53c2\u8003\u4e0a\u4e0b\u6587\u4e2d\u6ca1\u6709\u76f8\u5173\u4fe1\u606f\uff0c\u8bf7\u56de\u7b54\uff1a\u201c\u6587\u6863\u4e2d\u6ca1\u6709\u627e\u5230\u660e\u786e\u7b54\u6848\u3002\u201d
4. \u5982\u679c\u7528\u6237\u95ee\u7684\u662f\u603b\u7ed3\u3001\u653b\u7565\u3001\u4ea4\u901a\u8def\u7ebf\u3001\u5230\u8fbe\u65b9\u5f0f\u3001\u5b66\u4e60\u5efa\u8bae\u3001\u590d\u4e60\u8ba1\u5212\u3001\u65f6\u95f4\u4f30\u7b97\u3001\u91cd\u70b9\u63d0\u70bc\u3001\u96be\u5ea6\u5224\u65ad\u3001\u5982\u4f55\u51c6\u5907\u8003\u8bd5\u7b49\u95ee\u9898\uff0c\u53ef\u4ee5\u57fa\u4e8e\u53c2\u8003\u4e0a\u4e0b\u6587\u6574\u7406\u6210\u6e05\u6670\u6b65\u9aa4\u6216\u5efa\u8bae\u3002
5. \u5bf9\u4e8e\u5b66\u4e60\u5efa\u8bae/\u4f30\u7b97\u7c7b\u95ee\u9898\uff0c\u56de\u7b54\u5f00\u5934\u5fc5\u987b\u5199\uff1a\u201c\u4ee5\u4e0b\u662f\u57fa\u4e8e\u6587\u6863\u5185\u5bb9\u7684\u4f30\u7b97/\u5efa\u8bae\u3002\u201d
6. \u5982\u679c\u7528\u6237\u95ee\u201c\u9700\u8981\u591a\u4e45\u590d\u4e60\u5b8c\u201d\u8fd9\u7c7b\u95ee\u9898\uff0c\u8bf7\u53c2\u8003\u6587\u6863\u5b57\u6570/\u957f\u5ea6\u3001\u77e5\u8bc6\u70b9\u6570\u91cf\u3001\u662f\u5426\u9700\u8981\u80cc\u8bf5\u3001\u662f\u5426\u53ea\u662f\u5feb\u901f\u6d4f\u89c8\u3001\u662f\u5426\u8981\u51c6\u5907\u8003\u8bd5\uff0c\u5e76\u7ed9\u51fa\u5206\u6863\u5efa\u8bae\uff1a\u5feb\u901f\u6d4f\u89c8\u3001\u7406\u89e3\u590d\u4e60\u3001\u8003\u524d\u80cc\u8bf5/\u505a\u9898\u3002
7. \u5982\u679c\u7528\u6237\u95ee\u201c\u4ea4\u901a\u653b\u7565/\u5230\u8fbe\u65b9\u5f0f\u201d\uff0c\u8bf7\u4f18\u5148\u67e5\u627e GETTING TO THE MUSEUM\u3001access\u3001entrance\u3001metro\u3001bus\u3001acc\u00e8s\u3001entr\u00e9e\u3001m\u00e9tro\u3001Anfahrt\u3001Zugang\u3001Eingang \u7b49\u76f8\u5173\u5185\u5bb9\uff0c\u5e76\u6574\u7406\u6210\u4e2d\u6587\u8981\u70b9\u3002
8. \u4e0d\u8981\u7f16\u9020 PDF \u91cc\u6ca1\u6709\u7684\u4e8b\u5b9e\uff1b\u4f30\u7b97\u548c\u5efa\u8bae\u8981\u660e\u786e\u6807\u6ce8\u4e3a\u4f30\u7b97/\u5efa\u8bae\u3002
9. \u56de\u7b54\u4e2d\u53ef\u4ee5\u7b80\u77ed\u8bf4\u660e\uff1a\u672c\u6b21\u4f7f\u7528\u4e86 {len(relevant_chunks)} \u4e2a\u53c2\u8003\u7247\u6bb5\u3002

\u7528\u6237\u95ee\u9898\uff1a
{question}

\u53c2\u8003\u4e0a\u4e0b\u6587\uff1a
{rag_context}
"""
    return call_dashscope(prompt)


def get_current_pdf_text():
    stored_filename = session.get("current_pdf_stored_filename")
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


def render_result_html(result):
    if not result:
        return ""

    # \u8fd9\u662f\u672c\u5730\u8bfe\u7a0b\u9879\u76ee\u7684\u7b80\u5355\u6e32\u67d3\uff1a\u5148\u8f6c\u4e49 HTML\uff0c\u518d\u5c06 Markdown \u8f6c\u6210 HTML\u3002
    # \u8fd9\u6837\u53ef\u4ee5\u663e\u793a\u6807\u9898\u3001\u52a0\u7c97\u548c\u5217\u8868\uff0c\u540c\u65f6\u907f\u514d\u76f4\u63a5\u6e32\u67d3\u6a21\u578b\u8fd4\u56de\u7684 HTML\u3002
    safe_text = escape(result)
    if markdown is None:
        return safe_text.replace("\n", "<br>")

    return markdown.markdown(safe_text, extensions=["extra"])


@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    upload_message = ""

    if request.method == "POST":
        action = request.form.get("action")
        selected_pdf = request.form.get("select_pdf")
        delete_pdf = request.form.get("delete_pdf")
        pdf_file = request.files.get("pdf")

        if delete_pdf:
            safe_path = get_safe_pdf_path(delete_pdf)

            if not safe_path:
                upload_message = "\u6587\u4ef6\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\u3002"
            else:
                display_filename = get_original_filename(delete_pdf)
                os.remove(safe_path)
                file_index = load_file_index()
                file_index.pop(delete_pdf, None)
                save_file_index(file_index)
                upload_message = f"\u5df2\u5220\u9664\u6587\u4ef6\uff1a{display_filename}"

                current_path_name = os.path.basename(session.get("current_pdf_path", ""))
                if (
                    session.get("current_pdf_stored_filename") == delete_pdf
                    or session.get("current_pdf_name") == delete_pdf
                    or current_path_name == delete_pdf
                ):
                    session.pop("current_pdf_name", None)
                    session.pop("current_pdf_path", None)
                    session.pop("current_pdf_stored_filename", None)
                    session.pop("original_filename", None)

        elif selected_pdf:
            safe_path = get_safe_pdf_path(selected_pdf)

            if not safe_path:
                upload_message = "\u9009\u62e9\u7684 PDF \u6587\u4ef6\u4e0d\u5b58\u5728\u6216\u4e0d\u5408\u6cd5\u3002"
            else:
                display_filename = get_original_filename(selected_pdf)
                session["current_pdf_name"] = display_filename
                session["current_pdf_path"] = safe_path
                session["current_pdf_stored_filename"] = selected_pdf
                session["original_filename"] = display_filename
                upload_message = f"\u5f53\u524d\u4f7f\u7528\u6587\u6863\uff1a{display_filename}"

        elif action == "upload":
            if not pdf_file or pdf_file.filename == "":
                upload_message = "\u8bf7\u5148\u9009\u62e9 PDF \u6587\u4ef6"
            elif not is_pdf(pdf_file.filename):
                upload_message = "\u53ea\u652f\u6301\u4e0a\u4f20 PDF \u6587\u4ef6"
            else:
                os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                original_filename = pdf_file.filename.replace("\\", "/").split("/")[-1]
                stored_filename = make_saved_pdf_filename()
                save_path = os.path.join(get_upload_folder(), stored_filename)
                pdf_file.save(save_path)
                file_index = load_file_index()
                file_index[stored_filename] = {
                    "original_filename": original_filename,
                    "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                save_file_index(file_index)
                session["current_pdf_name"] = original_filename
                session["current_pdf_path"] = save_path
                session["current_pdf_stored_filename"] = stored_filename
                session["original_filename"] = original_filename
                upload_message = f"PDF \u4e0a\u4f20\u6210\u529f\uff1a{original_filename}"

        elif action == "summary":
            pdf_text, error = get_current_pdf_text()
            if error:
                result = error
            else:
                result = generate_summary(pdf_text)

        elif action == "ask":
            question = request.form.get("question", "").strip()

            if not question:
                result = "\u8bf7\u8f93\u5165\u4f60\u7684\u95ee\u9898\u3002"
            else:
                pdf_text, error = get_current_pdf_text()
                if error:
                    result = error
                else:
                    result = answer_question(pdf_text, question)

    if not upload_message and session.get("current_pdf_name"):
        upload_message = f"\u5f53\u524d PDF\uff1a{session.get('current_pdf_name')}"

    result_html = render_result_html(result)
    return render_template(
        "index.html",
        result_html=result_html,
        upload_message=upload_message,
        uploaded_files=get_uploaded_files(),
        current_pdf_name=session.get("current_pdf_name"),
        current_pdf_stored_filename=session.get("current_pdf_stored_filename"),
    )


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.run(debug=True)
