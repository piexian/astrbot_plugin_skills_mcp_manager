"""Reply language settings shared by report review and session delivery."""

LANGUAGE_INSTRUCTIONS = {
    "简体中文": "请使用简体中文撰写审查报告。",
    "English": "Write the review report in English.",
    "日本語": "審査レポートは日本語で記述してください。",
}

CONFIRMATION_WORDS = {
    "简体中文": "确认",
    "English": "confirm",
    "日本語": "確認",
}


def confirmation_word(language: str) -> str:
    return CONFIRMATION_WORDS[normalize_language(language)]


def normalize_language(value: str) -> str:
    return value if value in LANGUAGE_INSTRUCTIONS else "简体中文"


def language_instruction(value: str) -> str:
    return LANGUAGE_INSTRUCTIONS[normalize_language(value)]
