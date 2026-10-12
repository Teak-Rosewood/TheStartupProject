_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def valid_gstin_for(state: str, pan: str = "AAAPL1234C") -> str:
    body = f"{state}{pan}1Z"
    total = 0
    for i, ch in enumerate(body):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return body + _CHARS[(36 - total % 36) % 36]
