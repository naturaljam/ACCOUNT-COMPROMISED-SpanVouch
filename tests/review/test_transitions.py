from spanvouch.review.transitions import ReviewRoute, next_route
from tests.review.factories import (
    make_confirmed_case,
    make_pending_case,
    make_revision_one_case,
)


def test_pending_case_routes_to_initial_verification() -> None:
    assert next_route(make_pending_case()) is ReviewRoute.VERIFY_INITIAL


def test_revision_count_one_never_routes_to_second_revision() -> None:
    assert next_route(make_revision_one_case()) is not ReviewRoute.REQUEST_REVISION


def test_terminal_case_routes_to_end() -> None:
    assert next_route(make_confirmed_case()) is ReviewRoute.END
