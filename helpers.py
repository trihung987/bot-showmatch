from datetime import datetime


def format_vnd(amount: int) -> str:
    return f"{amount:,.0f} VNĐ".replace(",", ".")


def format_vn_time(dt: datetime) -> str:
    """Format: 14:30 - Ngày 25/12/2026"""
    return dt.strftime("%H:%M - Ngày %d/%m/%Y")


def get_elo_display(elo_str: str) -> str:
    try:
        e_type, e_min, e_max = elo_str.split(":")
        mapping = {
            "all": "Tự do",
            "range": f"Từ {e_min} đến {e_max}",
            "under": f"≤ {e_min}",
            "above": f"≥ {e_min}",
        }
        return mapping.get(e_type, "Không xác định")
    except Exception:
        return "Không xác định"
