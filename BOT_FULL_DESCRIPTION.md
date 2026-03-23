# Bot Description (Simple Guide)

This bot helps you track who owes money.
It has only 3 buttons:

- `In`
- `Balance`
- `Close`

---

## `In`

### What this button does
Use `In` when someone gave you money and you want the debt saved in the bot.

### What happens after click
1. Bot asks you to write amount.
2. You send amount:
   - `100`
   - `100 USD`
   - `100.5 EUR`
3. Bot creates a personal link with this amount.
4. Bot sends:
   - the link
   - `Forward Loan` button (fast forwarding)

### What the forwarded person sees
When they open the link, they see:
- your name
- amount + currency
- `Approve` button to confirm the loan

If they approve:
- transaction is saved in database
- balances are updated

### Notes
- If you write integer, bot shows `100` (not `100.00`).
- If you write decimal, bot keeps decimals (example `100.5`).

---

## `Balance`

### What this button does
Shows all open (non-zero) balances with people.

### Message format
Bot sends:

`Your balance:`

Then lines like:

- `FriendName + 50 USD` -> you owe this friend
- `FriendName - 30 ILS` -> this friend owes you

Only non-zero balances are shown.

---

## `Close`

### What this button does
Lets you close debt with a selected person.

### What happens after click
1. Bot sends a list of people with open balances as buttons.
2. You click a person.
3. Bot sets your balances with this person to `0`.

### Result
After closing, this person should disappear from `Balance` (unless new debt is created later).

---

## Main Flow (Short)

1. You receive money -> tap `In` -> send amount -> forward link.
2. Other person opens link -> taps `Approve`.
3. Open debts visible in `Balance`.
4. If needed, settle manually using `Close`.
