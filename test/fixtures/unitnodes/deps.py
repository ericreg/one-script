@register
class Child(Base, role=Mixin):
    pass


@register
def build(item=DEFAULT, *, option=OPTION):
    return Helper(item, option)


VALUE = factory(Base)
ANNOTATED: int = factory(DEFAULT)
TOTAL += step


class UsesOnlyInBody:
    def make(self):
        return LateThing()
