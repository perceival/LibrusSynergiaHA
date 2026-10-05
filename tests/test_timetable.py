"""Testy planu lekcji: helpery, pobieranie z klienta i czujniki."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from librus_apix.exceptions import ParseError, TokenError
from librus_apix.timetable import Period

from custom_components.librus_apix import LibrusApiClient
from custom_components.librus_apix import sensor as sensor_mod
from custom_components.librus_apix.sensor import (
    LibrusNastepnaLekcjaSensor,
    LibrusPlanDzisSensor,
    _aktualna_i_nastepna,
    _lekcje_dnia,
    _parse_godzina,
)


def _lekcja(data, numer, od, do, przedmiot="Matematyka"):
    return {
        "data": data,
        "numer": numer,
        "od": od,
        "do": do,
        "przedmiot": przedmiot,
        "nauczyciel_sala": "Jan Kowalski - 12",
        "uwagi": [],
        "przerwa_od": None,
        "przerwa_do": None,
    }


PLAN = [
    # poniedzialek 2026-10-05
    _lekcja("2026-10-05", 1, "08:00", "08:45", "Matematyka"),
    _lekcja("2026-10-05", 2, "08:55", "09:40", "Polski"),
    # wtorek 2026-10-06
    _lekcja("2026-10-06", 1, "08:00", "08:45", "Historia"),
]


# --- helpery ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tekst, oczekiwane",
    [
        ("08:05", (8, 5)),
        ("8:05", (8, 5)),
        (" 14:30:00 ", (14, 30)),
        ("", None),
        ("abc", None),
        (None, None),
        (805, None),
    ],
)
def test_parse_godzina(tekst, oczekiwane):
    wynik = _parse_godzina(tekst)
    if oczekiwane is None:
        assert wynik is None
    else:
        assert (wynik.hour, wynik.minute) == oczekiwane


def test_lekcje_dnia_sortuje_i_filtruje():
    plan = [PLAN[1], PLAN[2], PLAN[0]]
    wynik = _lekcje_dnia(plan, date(2026, 10, 5))
    assert [l["numer"] for l in wynik] == [1, 2]
    assert _lekcje_dnia(plan, date(2026, 10, 7)) == []


def test_aktualna_i_nastepna_w_trakcie_lekcji():
    trwajaca, nastepna = _aktualna_i_nastepna(PLAN, datetime(2026, 10, 5, 8, 30))
    assert trwajaca["przedmiot"] == "Matematyka"
    assert nastepna["przedmiot"] == "Polski"


def test_aktualna_i_nastepna_na_przerwie():
    trwajaca, nastepna = _aktualna_i_nastepna(PLAN, datetime(2026, 10, 5, 8, 50))
    assert trwajaca is None
    assert nastepna["przedmiot"] == "Polski"


def test_nastepna_po_ostatniej_lekcji_dnia_to_nastepny_dzien():
    trwajaca, nastepna = _aktualna_i_nastepna(PLAN, datetime(2026, 10, 5, 15, 0))
    assert trwajaca is None
    assert nastepna["przedmiot"] == "Historia"
    assert nastepna["data"] == "2026-10-06"


def test_nastepna_gdy_plan_pusty_lub_wyczerpany():
    assert _aktualna_i_nastepna([], datetime(2026, 10, 5, 8, 0)) == (None, None)
    assert _aktualna_i_nastepna(PLAN, datetime(2026, 10, 7, 8, 0)) == (None, None)


def test_koniec_lekcji_jest_wylaczny():
    # dokladnie o 08:45 lekcja sie konczy, nie trwa juz
    trwajaca, _ = _aktualna_i_nastepna(PLAN, datetime(2026, 10, 5, 8, 45))
    assert trwajaca is None


def test_uszkodzone_wpisy_sa_pomijane():
    plan = [
        {"data": "zla-data", "od": "08:00", "do": "08:45", "przedmiot": "X", "numer": 1},
        {"data": "2026-10-05", "od": "??", "do": "08:45", "przedmiot": "Y", "numer": 2},
        PLAN[0],
    ]
    trwajaca, nastepna = _aktualna_i_nastepna(plan, datetime(2026, 10, 5, 7, 0))
    assert trwajaca is None
    assert nastepna["przedmiot"] == "Matematyka"


# --- klient ----------------------------------------------------------------


def _period(subject, number, day, od="08:00", do="08:45", info=None):
    return Period(
        subject=subject,
        teacher_and_classroom="Jan Kowalski - 12" if subject else "",
        date=day,
        date_from=od,
        date_to=do,
        weekday="Monday",
        info=info or {},
        number=number,
        next_recess_from="08:45",
        next_recess_to="08:55",
    )


def _klient():
    klient = LibrusApiClient("u", "p")
    klient._client = MagicMock()
    klient._token = "token"
    return klient


def test_klient_pobiera_dwa_tygodnie_i_pomija_puste_okienka():
    tydzien = [
        [_period("Matematyka", 1, "2026-10-05"), _period("", 2, "2026-10-05")],
        [_period("Historia", 1, "2026-10-06", info={"odwolane": {}})],
    ]
    with patch("librus_apix.timetable.get_timetable", return_value=tydzien) as get:
        wynik = asyncio.run(_klient().async_get_timetable())

    assert get.call_count == 2  # biezacy + nastepny tydzien
    poniedzialki = [c.args[1] for c in get.call_args_list]
    assert all(p.weekday() == 0 for p in poniedzialki)
    assert (poniedzialki[1] - poniedzialki[0]).days == 7
    przedmioty = [l["przedmiot"] for l in wynik]
    assert "" not in przedmioty
    assert przedmioty.count("Matematyka") == 2  # po jednej na tydzien (ta sama atrapa)
    historia = next(l for l in wynik if l["przedmiot"] == "Historia")
    assert historia["uwagi"] == ["odwolane"]
    assert historia["nauczyciel_sala"] == "Jan Kowalski - 12"


def test_klient_oba_tygodnie_parseerror_zwraca_none_by_zachowac_plan():
    # np. strona bledu Librusa lub ferie: nie nadpisuj dobrego planu pusta lista
    with patch("librus_apix.timetable.get_timetable", side_effect=ParseError("brak")):
        assert asyncio.run(_klient().async_get_timetable()) is None


def test_klient_jeden_tydzien_parseerror_zwraca_drugi():
    tydzien = [[_period("Matematyka", 1, "2026-10-12")]]
    with patch(
        "librus_apix.timetable.get_timetable",
        side_effect=[ParseError("ferie"), tydzien],
    ):
        wynik = asyncio.run(_klient().async_get_timetable())
    assert [l["data"] for l in wynik] == ["2026-10-12"]


def test_klient_laczy_i_sortuje_dwa_tygodnie():
    tydzien_1 = [[_period("Historia", 2, "2026-10-06"), _period("Polski", 1, "2026-10-06")]]
    tydzien_2 = [[_period("Fizyka", 1, "2026-10-13")]]
    with patch(
        "librus_apix.timetable.get_timetable", side_effect=[tydzien_1, tydzien_2]
    ):
        wynik = asyncio.run(_klient().async_get_timetable())
    assert [(l["data"], l["numer"], l["przedmiot"]) for l in wynik] == [
        ("2026-10-06", 1, "Polski"),
        ("2026-10-06", 2, "Historia"),
        ("2026-10-13", 1, "Fizyka"),
    ]


def test_klient_zwykly_blad_nie_resetuje_logowania():
    klient = _klient()
    with patch("librus_apix.timetable.get_timetable", side_effect=RuntimeError("zepsuty layout")):
        assert asyncio.run(klient.async_get_timetable()) is None
    assert klient._client is not None and klient._token == "token"


def test_klient_ponawia_po_wygasnieciu_tokenu():
    klient = _klient()
    wywolania = []

    async def _auth_ok():
        klient._client = MagicMock()
        klient._token = "nowy"
        wywolania.append(1)
        return True

    klient.async_authenticate = _auth_ok
    tydzien = [[_period("Matematyka", 1, "2026-10-05")]]
    with patch(
        "librus_apix.timetable.get_timetable",
        side_effect=[TokenError("wygasl"), tydzien, tydzien],
    ):
        wynik = asyncio.run(klient.async_get_timetable())
    assert wywolania == [1]  # jedno ponowne logowanie
    assert wynik and wynik[0]["przedmiot"] == "Matematyka"


def test_klient_przy_bledzie_zwraca_none_po_dwoch_probach():
    klient = _klient()

    async def _auth_fail():
        return False

    klient.async_authenticate = _auth_fail
    klient._client = None  # wymusza ponowne uwierzytelnienie, ktore sie nie udaje
    assert asyncio.run(klient.async_get_timetable()) is None


def test_klient_token_error_prowadzi_do_none():
    klient = _klient()

    async def _auth_ok():
        klient._client = MagicMock()
        klient._token = "token"
        return True

    klient.async_authenticate = _auth_ok
    with patch("librus_apix.timetable.get_timetable", side_effect=TokenError("wygasl")):
        assert asyncio.run(klient.async_get_timetable()) is None


# --- czujniki --------------------------------------------------------------


def _koordynator(plan):
    return SimpleNamespace(data={"plan_lekcji": plan}, async_add_listener=MagicMock())


def _czujnik(klasa, plan, teraz):
    czujnik = klasa(_koordynator(plan), SimpleNamespace(entry_id="e1"))
    patcher = patch.object(
        sensor_mod.dt_util, "now", return_value=teraz.astimezone()
    )
    patcher.start()
    return czujnik, patcher


def test_czujnik_plan_dzis():
    czujnik, p = _czujnik(LibrusPlanDzisSensor, PLAN, datetime(2026, 10, 5, 7, 0))
    try:
        assert czujnik.native_value == 2
        attrs = czujnik.extra_state_attributes
        assert attrs["dzien"] == "poniedzialek"
        assert attrs["pierwsza_lekcja_od"] == "08:00"
        assert attrs["ostatnia_lekcja_do"] == "09:40"
        assert [l["przedmiot"] for l in attrs["lekcje"]] == ["Matematyka", "Polski"]
    finally:
        p.stop()


def test_czujnik_plan_dzis_dzien_bez_lekcji():
    czujnik, p = _czujnik(LibrusPlanDzisSensor, PLAN, datetime(2026, 10, 10, 7, 0))
    try:
        assert czujnik.native_value == 0
        attrs = czujnik.extra_state_attributes
        assert attrs["dzien"] == "sobota"
        assert attrs["pierwsza_lekcja_od"] is None
    finally:
        p.stop()


def test_czujnik_nastepna_lekcja_w_trakcie():
    czujnik, p = _czujnik(LibrusNastepnaLekcjaSensor, PLAN, datetime(2026, 10, 5, 8, 30))
    try:
        assert czujnik.native_value == "Polski"
        attrs = czujnik.extra_state_attributes
        assert attrs["trwa_teraz"] == "Matematyka"
        assert "za_minut" not in attrs  # zmieniaby sie co minute i zapisywal historie
        assert attrs["od"] == "08:55"
    finally:
        p.stop()


def test_czujnik_nastepna_lekcja_w_weekend_to_poniedzialek():
    plan = PLAN + [_lekcja("2026-10-12", 1, "08:00", "08:45", "Fizyka")]
    czujnik, p = _czujnik(LibrusNastepnaLekcjaSensor, plan, datetime(2026, 10, 10, 12, 0))
    try:
        assert czujnik.native_value == "Fizyka"
        assert czujnik.extra_state_attributes["dzien"] == "poniedzialek"
    finally:
        p.stop()


def test_czujnik_nastepna_lekcja_brak_planu():
    czujnik, p = _czujnik(LibrusNastepnaLekcjaSensor, [], datetime(2026, 10, 5, 8, 30))
    try:
        assert czujnik.native_value == "brak"
        assert czujnik.extra_state_attributes == {"trwa_teraz": None}
    finally:
        p.stop()


# --- sanityzacja i limity (dane ze scrapowanego HTML) -----------------------


def test_tekst_czysci_i_skraca():
    from custom_components.librus_apix import _tekst

    assert _tekst(None) == ""
    assert _tekst("  Mat\n\tema\x00tyka  ") == "Mat ema tyka"
    assert len(_tekst("x" * 500)) == 100
    assert _tekst("abcdef", 3) == "abc"


def test_klient_ogranicza_liczbe_lekcji_dziennie_i_uwag():
    za_duzo = [_period(f"Przedmiot {i}", i, "2026-10-05") for i in range(40)]
    za_duzo[0].info = {f"uwaga{i}": {} for i in range(20)}
    with patch("librus_apix.timetable.get_timetable", return_value=[za_duzo]):
        wynik = asyncio.run(_klient().async_get_timetable())
    # dwa tygodnie x max 15 lekcji w dniu
    assert len(wynik) == 30
    assert len(wynik[0]["uwagi"]) == 5


def test_klient_czysci_pola_tekstowe():
    p = _period("Mate\x00matyka\n", 1, "2026-10-05")
    p.teacher_and_classroom = "A" * 400
    with patch("librus_apix.timetable.get_timetable", return_value=[[p]]):
        wynik = asyncio.run(_klient().async_get_timetable())
    assert wynik[0]["przedmiot"] == "Mate matyka"
    assert len(wynik[0]["nauczyciel_sala"]) == 100
