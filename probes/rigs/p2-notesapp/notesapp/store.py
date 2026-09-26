class AccountStore:
    def __init__(self):
        self._accounts = {}

    def create_account(self, name):
        account_id = f"acc-{len(self._accounts) + 1}"
        self._accounts[account_id] = {"name": name, "notes": []}
        return account_id

    def get_account(self, account_id):
        return self._accounts[account_id]

    def add_note(self, account_id, text):
        self._accounts[account_id]["notes"].append(text)
