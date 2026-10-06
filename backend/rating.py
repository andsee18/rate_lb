from dataclasses import dataclass


@dataclass(frozen=True)
class RatingStatus:
    label: str
    class_name: str


def get_status(score: float, gender: str = "male") -> RatingStatus:
    if gender == "female":
        if score >= 8.3:
            return RatingStatus("True Eva", "status-true-eve")
        if score >= 6.8:
            return RatingStatus("Stacy", "status-stacy")
        if score >= 5.6:
            return RatingStatus("HTB", "status-htb")
        if score >= 4.8:
            return RatingStatus("MTB", "status-mtb")
        if score >= 4:
            return RatingStatus("LTB", "status-ltb")
        if score >= 3:
            return RatingStatus("Sub-5", "status-sub-5")
        return RatingStatus("Sub-3", "status-sub-3")
    if score >= 8.3:
        return RatingStatus("True Adam", "status-true-adam")
    if score >= 6.8:
        return RatingStatus("Chad", "status-chad")
    if score >= 5.6:
        return RatingStatus("HTN", "status-htn")
    if score >= 4.8:
        return RatingStatus("MTN", "status-mtn")
    if score >= 4:
        return RatingStatus("LTN", "status-ltn")
    if score >= 3:
        return RatingStatus("Sub-5", "status-sub-5")
    return RatingStatus("Sub-3", "status-sub-3")
