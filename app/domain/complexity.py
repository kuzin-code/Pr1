"""Автоматическая оценка балла сложности (технолог исправляет при необходимости)."""

MANY_THREADS = 5


def estimate_complexity(tolerance_grade: int | None, thread_count: int = 0) -> int:
    """Балл 1…5 по самому точному квалитету и числу резьб."""
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
    if thread_count >= MANY_THREADS:
        score += 1
    return min(score, 5)
