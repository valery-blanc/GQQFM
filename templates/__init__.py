from templates.calendar_strangle import CALENDAR_STRANGLE
from templates.double_calendar import DOUBLE_CALENDAR
from templates.reverse_iron_condor import REVERSE_IRON_CONDOR_CALENDAR
from templates.call_diagonal_backspread import CALL_DIAGONAL_BACKSPREAD
from templates.call_ratio_diagonal import CALL_RATIO_DIAGONAL

ALL_TEMPLATES = {
    CALENDAR_STRANGLE.name: CALENDAR_STRANGLE,
    DOUBLE_CALENDAR.name: DOUBLE_CALENDAR,
    REVERSE_IRON_CONDOR_CALENDAR.name: REVERSE_IRON_CONDOR_CALENDAR,
    CALL_DIAGONAL_BACKSPREAD.name: CALL_DIAGONAL_BACKSPREAD,
    CALL_RATIO_DIAGONAL.name: CALL_RATIO_DIAGONAL,
}
