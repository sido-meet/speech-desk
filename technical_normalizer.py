import re


TECH_TERMS = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "openai": "OpenAI",
    "chatgpt": "ChatGPT",
    "qwen": "Qwen",
    "vosk": "Vosk",
    "cuda": "CUDA",
    "gpu": "GPU",
    "cpu": "CPU",
    "api": "API",
    "json": "JSON",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
}

SUBSCRIPT = str.maketrans("0123456789+-()", "₀₁₂₃₄₅₆₇₈₉₊₋₍₎")
SUPERSCRIPT = str.maketrans("0123456789+-()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾")


def _normalize_terms(text: str) -> str:
    for source, target in TECH_TERMS.items():
        text = re.sub(rf"(?i)(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])", target, text)
    return text


def _normalize_spoken_formulas(text: str) -> str:
    digit = r"(?:2|二|两|two)"
    text = re.sub(rf"(?i)(?<![A-Za-z])H\s*{digit}\s*O\s*{digit}(?![A-Za-z])", "H2O2", text)
    text = re.sub(r"(?i)(?<![A-Za-z])H\s*(?:2|二|两|two)\s*O(?![A-Za-z])", "H2O", text)
    text = re.sub(r"(?i)(?<![A-Za-z])CO\s*(?:2|二|两|two)(?![A-Za-z])", "CO2", text)
    text = re.sub(r"(?i)(?<![A-Za-z])O\s*(?:2|二|两|two)(?![A-Za-z])", "O2", text)
    text = re.sub(r"(?i)(?<![A-Za-z])N\s*(?:2|二|两|two)(?![A-Za-z])", "N2", text)
    return text


def _render_chemical_formula(match: re.Match) -> str:
    formula = match.group(0)
    return re.sub(r"\d+", lambda item: item.group(0).translate(SUBSCRIPT), formula)


def _normalize_chemical_formulas(text: str) -> str:
    # Require at least two element groups and at least one digit to avoid changing normal acronyms.
    pattern = re.compile(r"(?<![A-Za-z])(?=[A-Za-z0-9]*\d)(?:[A-Z][a-z]?\d*){2,}(?![A-Za-z])")
    return pattern.sub(_render_chemical_formula, text)


def _normalize_math(text: str) -> str:
    text = re.sub(r"(?i)\b([A-Za-z])\s+(?:squared|的平方)\b", lambda m: f"{m.group(1)}²", text)
    text = re.sub(r"(?i)\b([A-Za-z])\s+(?:cubed|的立方)\b", lambda m: f"{m.group(1)}³", text)
    text = re.sub(r"(?i)\b([A-Za-z])\s*(?:to the power of|的)(\d+)\b", lambda m: f"{m.group(1)}{m.group(2).translate(SUPERSCRIPT)}", text)
    return text


def normalize_technical_text(text: str) -> str:
    text = text.strip()
    text = _normalize_terms(text)
    text = _normalize_spoken_formulas(text)
    text = _normalize_chemical_formulas(text)
    text = _normalize_math(text)
    return text
