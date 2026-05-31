"""Юніт-тести core.user_profile.UserProfile (з фейковою БД у пам'яті)."""
from core.user_profile import UserProfile, PROFILE_KEY


class FakeDB:
    def __init__(self): self.store = {}
    def get_setting(self, k): return self.store.get(k)
    def set_setting(self, k, v): self.store[k] = v


def test_defaults():
    up = UserProfile(FakeDB())
    assert up.get("address_form") == "ти"
    assert up.get("language") == "uk"
    assert set(up.all().keys()) >= {"name", "interests", "address_form"}


def test_update_persists_and_reloads():
    db = FakeDB()
    up = UserProfile(db)
    up.update(name="Степан", address_form="ви")
    assert up.get("name") == "Степан" and up.get("address_form") == "ви"
    assert PROFILE_KEY in db.store          # збережено у settings
    # новий інстанс на тій самій БД підтягує збережене
    assert UserProfile(db).get("name") == "Степан"


def test_update_ignores_unknown_keys():
    up = UserProfile(FakeDB())
    up.update(unknown_field="x")
    assert "unknown_field" not in up.all()


def test_add_interest_dedup():
    up = UserProfile(FakeDB())
    up.add_interest("музика")
    up.add_interest("Музика")   # той самий (case-insensitive)
    assert up.get("interests") == ["музика"]


def test_add_interest_caps_at_12():
    up = UserProfile(FakeDB())
    for i in range(15):
        up.add_interest(f"тема{i}")
    assert len(up.get("interests")) == 12


def test_remove_interest():
    up = UserProfile(FakeDB())
    up.add_interest("спорт")
    assert up.remove_interest("Спорт") is True
    assert up.get("interests") == []
    assert up.remove_interest("неіснує") is False


def test_as_prompt_block():
    up = UserProfile(FakeDB())
    assert up.as_prompt_block() == ""   # порожній профіль
    up.update(name="Степан")
    up.add_interest("AI")
    block = up.as_prompt_block()
    assert "Степан" in block and "AI" in block


def test_load_corrupt_json_keeps_defaults():
    db = FakeDB()
    db.store[PROFILE_KEY] = "{зламаний json"
    up = UserProfile(db)   # не має кидати
    assert up.get("address_form") == "ти"
