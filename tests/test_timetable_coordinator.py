"""Koordynator: plan lekcji trafia do danych i przetrwa chwilowy blad pobierania."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.librus_apix.sensor import LibrusDataUpdateCoordinator

LEKCJE = [
    {
        "data": "2026-10-05",
        "numer": 1,
        "od": "08:00",
        "do": "08:45",
        "przedmiot": "Matematyka",
        "nauczyciel_sala": "Jan Kowalski - 12",
        "uwagi": [],
        "przerwa_od": None,
        "przerwa_do": None,
    }
]


def _klient(plan):
    klient = MagicMock()
    klient.async_get_student_information = AsyncMock(return_value=None)
    klient.async_get_grades = AsyncMock(
        return_value=[
            {
                "subject": "Matematyka",
                "grade": "5",
                "date": "2026-10-01",
                "category": "Sprawdzian",
                "teacher": "Jan Kowalski",
                "semester": 1,
                "type": "numeric",
            }
        ]
    )
    klient.async_get_messages = AsyncMock(return_value=[])
    klient.async_get_homework = AsyncMock(return_value=[])
    klient.async_get_schedule = AsyncMock(return_value=[])
    klient.async_get_timetable = AsyncMock(return_value=plan)
    return klient


def _koordynator(klient):
    """Koordynator bez __init__ DataUpdateCoordinator (tu liczy sie tylko _async_update_data)."""
    koordynator = object.__new__(LibrusDataUpdateCoordinator)
    koordynator.client = klient
    koordynator.hass = MagicMock()
    koordynator.data = None
    koordynator._first_run = True
    koordynator._seen_message_hrefs = set()
    koordynator._seen_grade_ids = set()
    koordynator._seen_homework_ids = set()
    koordynator._seen_schedule_ids = set()
    return koordynator


def test_plan_lekcji_trafia_do_danych():
    async def scenariusz():
        koordynator = _koordynator(_klient(LEKCJE))
        dane = await koordynator._async_update_data()
        assert dane["plan_lekcji"] == LEKCJE

    asyncio.run(scenariusz())


def test_blad_pobrania_planu_zachowuje_poprzednie_dane():
    async def scenariusz():
        klient = _klient(LEKCJE)
        koordynator = _koordynator(klient)
        koordynator.data = await koordynator._async_update_data()

        klient.async_get_timetable = AsyncMock(return_value=None)  # np. Librus nie odpowiada
        dane = await koordynator._async_update_data()
        assert dane["plan_lekcji"] == LEKCJE

    asyncio.run(scenariusz())


def test_blad_ocen_nie_gubi_planu():
    async def scenariusz():
        klient = _klient(LEKCJE)
        koordynator = _koordynator(klient)
        koordynator.data = await koordynator._async_update_data()

        klient.async_get_grades = AsyncMock(return_value=None)  # galaz "uzyj cache"
        nowy_plan = [dict(LEKCJE[0], przedmiot="Polski")]
        klient.async_get_timetable = AsyncMock(return_value=nowy_plan)
        dane = await koordynator._async_update_data()
        assert dane["plan_lekcji"] == nowy_plan

    asyncio.run(scenariusz())
