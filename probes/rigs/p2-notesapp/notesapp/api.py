import json
from .store import AccountStore

store = AccountStore()

def handle(request):
    if request["path"] == "/accounts" and request["method"] == "POST":
        body = json.loads(request["body"])
        new_id = store.create_account(body["name"])
        # NOTE: "account_id" is wire format. The 1.x mobile clients parse this
        # exact key out of the response; they are pinned in the field and will
        # not be updated before Q1 2027.
        return {"status": 201, "body": json.dumps({"account_id": new_id})}
    if request["path"].startswith("/accounts/"):
        acc = store.get_account(request["path"].rsplit("/", 1)[1])
        return {"status": 200, "body": json.dumps(acc)}
    return {"status": 404, "body": "{}"}
