"""Автоматическая оценка балла сложности (технолог исправляет при необходимости)."""

MANY_THREADS = 5


FINE_RA = 0.8  # Ra ≤ 0,8 мкм — чистовая обработка


def estimate_complexity(tolerance_grade: int | None, thread_count: int = 0, ra_min: float | None = None,
                        form_tolerances: bool = False) -> int:
    """Балл 1…5 по самому точному квалитету; +1 за много резьб, Ra ≤ 0,8 или допуски формы и
    расположения (ГОСТ 2.308) — надбавка не больше одного балла."""
    grade = tolerance_grade if tolerance_grade is not None else 12
    if grade <= 6:
        score = 5
    elif grade == 7:
        score = 4
    elif grade <= 9:
        score = 3
    elif grade <= 11:
        score = 2
    else:
        score = 1
    if thread_count >= MANY_THREADS or form_tolerances or (ra_min is not None and ra_min <= FINE_RA):
        score += 1
    return min(score, 5)
