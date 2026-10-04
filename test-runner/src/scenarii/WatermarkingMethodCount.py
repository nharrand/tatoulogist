"""Check that a group implemented one watermarking method per member."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, ClassVar

from scenarii.Scenario import Scenario
from steps.GetWatermarkingMethods import GetWatermarkingMethods

if TYPE_CHECKING:
    from model.Group import Group


class WatermarkingMethodCount(Scenario):
    """get-watermarking-methods must list as many methods as the group has
    members (Members column of groups.csv), not counting the methods that
    ship with the reference server. Duplicate names are counted once.
    """

    description = ("Check that the group offers one watermarking method per member, "
                   "excluding the reference methods")

    #: Methods of the reference implementation, not counted.
    EXCLUDED: ClassVar[frozenset[str]] = frozenset({"toy-eof", "bash-bridge-eof"})

    def execute(self, group: Group) -> None:
        self.require(group.members is not None,
                     f"the number of members of {group.name} is set in groups.csv")

        result = self.step(GetWatermarkingMethods())
        self.check_status(result, 200, "get-watermarking-methods succeeds", required=True)
        self.check_fields(result, {"count": int, "methods": list},
                          "get-watermarking-methods returns count and methods",
                          required=True)

        methods = result.response_json["methods"]
        names = [m.get("name") for m in methods if isinstance(m, dict)]
        malformed = [m for m in methods
                     if not (isinstance(m, dict) and isinstance(m.get("name"), str))]
        self.check(not malformed, "every method has a name", {"malformed": malformed}
                   if malformed else None)
        self.check(result.response_json["count"] == len(methods),
                   "method count matches the methods list",
                   {"count": result.response_json["count"], "listed": len(methods)})

        names = [n for n in names if isinstance(n, str)]
        duplicates = sorted(n for n, k in Counter(names).items() if k > 1)
        self.check(not duplicates, "method names are unique",
                   {"duplicates": duplicates} if duplicates else None)

        own = sorted(set(names) - self.EXCLUDED)
        self.check(len(own) == group.members,
                   f"{group.members} watermarking method(s) besides the reference ones",
                   {"expected": group.members, "found": len(own), "methods": own,
                    "excluded": sorted(set(names) & self.EXCLUDED)})
