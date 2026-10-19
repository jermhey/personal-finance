"""Cost of living index for showing results in today's dollars.

Annual averages of a US consumer price index, 1982-84 = 100.
Source: the public CPI-U series mirrored by the datasets/cpi-us project.
2025 is missing October in that file. 2026 runs through August.
"""

from __future__ import annotations

from datetime import date

CPI: dict[int, float] = {
    1913: 9.883,
    1914: 10.017,
    1915: 10.108,
    1916: 10.883,
    1917: 12.825,
    1918: 15.042,
    1919: 17.333,
    1920: 20.042,
    1921: 17.850,
    1922: 16.750,
    1923: 17.050,
    1924: 17.125,
    1925: 17.542,
    1926: 17.700,
    1927: 17.358,
    1928: 17.158,
    1929: 17.158,
    1930: 16.700,
    1931: 15.208,
    1932: 13.642,
    1933: 12.933,
    1934: 13.383,
    1935: 13.725,
    1936: 13.867,
    1937: 14.383,
    1938: 14.092,
    1939: 13.908,
    1940: 14.008,
    1941: 14.725,
    1942: 16.333,
    1943: 17.308,
    1944: 17.592,
    1945: 17.992,
    1946: 19.517,
    1947: 22.325,
    1948: 24.042,
    1949: 23.808,
    1950: 24.067,
    1951: 25.958,
    1952: 26.550,
    1953: 26.767,
    1954: 26.850,
    1955: 26.775,
    1956: 27.183,
    1957: 28.092,
    1958: 28.858,
    1959: 29.150,
    1960: 29.575,
    1961: 29.892,
    1962: 30.250,
    1963: 30.625,
    1964: 31.017,
    1965: 31.508,
    1966: 32.458,
    1967: 33.358,
    1968: 34.783,
    1969: 36.683,
    1970: 38.825,
    1971: 40.492,
    1972: 41.817,
    1973: 44.400,
    1974: 49.308,
    1975: 53.817,
    1976: 56.908,
    1977: 60.608,
    1978: 65.233,
    1979: 72.575,
    1980: 82.408,
    1981: 90.925,
    1982: 96.500,
    1983: 99.600,
    1984: 103.883,
    1985: 107.567,
    1986: 109.608,
    1987: 113.625,
    1988: 118.258,
    1989: 123.967,
    1990: 130.658,
    1991: 136.192,
    1992: 140.317,
    1993: 144.458,
    1994: 148.225,
    1995: 152.383,
    1996: 156.850,
    1997: 160.517,
    1998: 163.008,
    1999: 166.575,
    2000: 172.200,
    2001: 177.067,
    2002: 179.875,
    2003: 183.958,
    2004: 188.883,
    2005: 195.292,
    2006: 201.592,
    2007: 207.342,
    2008: 215.303,
    2009: 214.537,
    2010: 218.056,
    2011: 224.939,
    2012: 229.594,
    2013: 232.957,
    2014: 236.736,
    2015: 237.017,
    2016: 240.007,
    2017: 245.120,
    2018: 251.107,
    2019: 255.657,
    2020: 258.811,
    2021: 270.970,
    2022: 292.655,
    2023: 304.702,
    2024: 313.689,
    2025: 321.943,
    2026: 331.655,
}



def level(day: date) -> float:
    """Annual index for a calendar year, using the nearest year outside the table."""

    if day.year in CPI:
        return CPI[day.year]
    years = sorted(CPI)
    if day.year < years[0]:
        return CPI[years[0]]
    return CPI[years[-1]]


def latest_year() -> int:
    return max(CPI)


def in_today_dollars(amount: float, on: date, today: date | None = None) -> float:
    base = level(today or date.today())
    then = level(on)
    if then <= 0:
        return amount
    return amount * base / then


def adjust_path(points, flows: list[tuple[date, float]], ending_value: float):
    """Scale each point into the dollars of the last point.

    `flows` are signed the way the replay stores them for return math:
    money added is negative, money taken out is positive. The ending
    balance is dropped when it is the final entry.
    """

    if not points:
        return [], None
    base_day = points[-1].date
    base = level(base_day)
    usable = list(flows)
    if (
        usable
        and ending_value
        and usable[-1][0] == base_day
        and abs(usable[-1][1] - ending_value) < 0.05
    ):
        usable = usable[:-1]
    by_date: dict[date, float] = {}
    for day, amount in usable:
        by_date[day] = by_date.get(day, 0.0) + (-amount)
    ordered = sorted(by_date)
    index = 0
    running = 0.0
    rows: list[tuple[float, float, float]] = []
    for point in points:
        while index < len(ordered) and ordered[index] <= point.date:
            day = ordered[index]
            then = level(day)
            running += by_date[day] * base / then if then else by_date[day]
            index += 1
        then = level(point.date)
        real_value = point.value * base / then if then else point.value
        rows.append((real_value, running, then))
    return rows, base
