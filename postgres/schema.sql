-- Debt bot schema for PostgreSQL
-- Sign convention summary:
--   * balances.net_amount > 0  => user_high owes user_low
--   * balances.net_amount < 0  => user_low owes user_high
--   * transactions.direction='out' means created_by paid out money (increases what counterparty owes created_by)
--   * transactions.direction='in'  means created_by received money (decreases what counterparty owes created_by)

begin;

create extension if not exists pgcrypto;

-- ---------- Enums ----------
do $$
begin
    create type public.friendship_status as enum ('pending', 'accepted', 'declined', 'blocked');
exception
    when duplicate_object then null;
end
$$;

do $$
begin
    create type public.invite_status as enum ('pending', 'accepted', 'declined', 'expired', 'revoked');
exception
    when duplicate_object then null;
end
$$;

do $$
begin
    create type public.transaction_status as enum ('pending', 'confirmed', 'rejected', 'reversed');
exception
    when duplicate_object then null;
end
$$;

do $$
begin
    create type public.transaction_direction as enum ('in', 'out');
exception
    when duplicate_object then null;
end
$$;

-- ---------- Helpers ----------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ---------- Profiles ----------
create table if not exists public.profiles (
    id uuid primary key default gen_random_uuid(),
    telegram_user_id bigint not null unique,
    telegram_username text,
    display_name text,
    default_currency text not null default 'ILS',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint profiles_telegram_user_id_positive_chk check (telegram_user_id > 0),
    constraint profiles_telegram_username_not_blank_chk check (
        telegram_username is null or length(trim(telegram_username)) > 0
    ),
    constraint profiles_display_name_not_blank_chk check (
        display_name is null or length(trim(display_name)) > 0
    ),
    constraint profiles_default_currency_supported_chk check (
        default_currency in ('ILS', 'USD', 'EUR', 'RUB')
    )
);

comment on table public.profiles is 'Telegram users known to the bot.';
comment on column public.profiles.telegram_user_id is 'Telegram numeric user ID (globally unique).';
comment on column public.profiles.default_currency is 'User default currency for commands where currency is omitted.';

create index if not exists idx_profiles_telegram_username
    on public.profiles (telegram_username);

-- Unique constraint on telegram_user_id already creates an index.

-- ---------- Friendships ----------
create table if not exists public.friendships (
    id uuid primary key default gen_random_uuid(),
    user_low uuid not null references public.profiles(id) on delete cascade,
    user_high uuid not null references public.profiles(id) on delete cascade,
    status public.friendship_status not null default 'pending',
    invited_by uuid not null references public.profiles(id) on delete restrict,
    accepted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint friendships_canonical_order_chk check (user_low < user_high),
    constraint friendships_distinct_users_chk check (user_low <> user_high),
    constraint friendships_pair_unique unique (user_low, user_high),
    constraint friendships_inviter_in_pair_chk check (invited_by in (user_low, user_high)),
    constraint friendships_accepted_state_chk check (
        (status = 'accepted' and accepted_at is not null)
        or
        (status <> 'accepted' and accepted_at is null)
    )
);

comment on table public.friendships is 'Canonical user pairs: always store smaller UUID in user_low and larger UUID in user_high.';
comment on column public.friendships.user_low is 'Lower UUID in canonical pair.';
comment on column public.friendships.user_high is 'Higher UUID in canonical pair.';

create index if not exists idx_friendships_user_low_status
    on public.friendships (user_low, status);

create index if not exists idx_friendships_user_high_status
    on public.friendships (user_high, status);

-- Unique (user_low, user_high) covers direct pair lookups.

-- ---------- Invites ----------
create table if not exists public.invites (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    inviter uuid not null references public.profiles(id) on delete cascade,
    invitee_username text,
    invitee_user_id bigint,
    status public.invite_status not null default 'pending',
    expires_at timestamptz not null,
    used_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint invites_code_not_blank_chk check (length(trim(code)) > 0),
    constraint invites_invitee_user_id_positive_chk check (
        invitee_user_id is null or invitee_user_id > 0
    ),
    constraint invites_invitee_username_not_blank_chk check (
        invitee_username is null or length(trim(invitee_username)) > 0
    ),
    constraint invites_used_state_chk check (
        (status = 'accepted' and used_at is not null)
        or
        (status <> 'accepted' and used_at is null)
    )
);

comment on table public.invites is 'Invitation codes generated by existing users to connect with new friends.';
comment on column public.invites.invitee_user_id is 'Telegram user ID if the invited user is known; nullable until first contact.';

create index if not exists idx_invites_inviter_status
    on public.invites (inviter, status);

create index if not exists idx_invites_invitee_user_id
    on public.invites (invitee_user_id);

create index if not exists idx_invites_status_expires_at
    on public.invites (status, expires_at);

-- ---------- Transactions ----------
create table if not exists public.transactions (
    id uuid primary key default gen_random_uuid(),
    friendship_id uuid not null references public.friendships(id) on delete cascade,
    created_by uuid not null references public.profiles(id) on delete restrict,
    direction public.transaction_direction not null,
    amount numeric(14, 2) not null,
    currency text not null,
    note text,
    status public.transaction_status not null default 'pending',
    confirmed_by uuid references public.profiles(id) on delete restrict,
    reverses_transaction_id uuid references public.transactions(id) on delete restrict,
    confirmed_at timestamptz,
    rejected_at timestamptz,
    reversed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint transactions_amount_positive_chk check (amount > 0),
    constraint transactions_currency_iso_chk check (
        currency in ('ILS', 'USD', 'EUR', 'RUB')
    ),
    constraint transactions_note_not_blank_chk check (
        note is null or length(trim(note)) > 0
    ),
    constraint transactions_confirmed_fields_chk check (
        (status = 'confirmed' and confirmed_by is not null and confirmed_at is not null)
        or
        (status <> 'confirmed' and confirmed_by is null and confirmed_at is null)
    ),
    constraint transactions_rejected_fields_chk check (
        (status = 'rejected' and rejected_at is not null)
        or
        (status <> 'rejected' and rejected_at is null)
    ),
    constraint transactions_reversed_fields_chk check (
        (status = 'reversed' and reversed_at is not null and reverses_transaction_id is not null)
        or
        (status <> 'reversed' and reversed_at is null and reverses_transaction_id is null)
    ),
    constraint transactions_no_self_reverse_chk check (
        reverses_transaction_id is null or reverses_transaction_id <> id
    )
);

comment on table public.transactions is 'Ledger transactions within a friendship, subject to confirmation workflow.';
comment on column public.transactions.direction is 'out = payer paid money out (counterparty debt increases); in = payer received money (counterparty debt decreases).';
comment on column public.transactions.reverses_transaction_id is 'References the original transaction when this row is a reversal entry.';

create index if not exists idx_transactions_friendship_status_created_at
    on public.transactions (friendship_id, status, created_at desc);

create index if not exists idx_transactions_friendship_created_at
    on public.transactions (friendship_id, created_at desc);

create index if not exists idx_transactions_created_by_created_at
    on public.transactions (created_by, created_at desc);

create unique index if not exists idx_transactions_reversal_unique
    on public.transactions (reverses_transaction_id)
    where reverses_transaction_id is not null;

-- ---------- Balances ----------
-- Design choice: one row per friendship per currency.
-- net_amount sign convention:
--   net_amount > 0  => user_high owes user_low
--   net_amount < 0  => user_low owes user_high
create table if not exists public.balances (
    id uuid primary key default gen_random_uuid(),
    friendship_id uuid not null references public.friendships(id) on delete cascade,
    currency text not null,
    net_amount numeric(14, 2) not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint balances_currency_iso_chk check (
        currency in ('ILS', 'USD', 'EUR', 'RUB')
    ),
    constraint balances_friendship_currency_unique unique (friendship_id, currency)
);

comment on table public.balances is 'Current net debt per friendship and currency.';
comment on column public.balances.net_amount is 'Positive means user_high owes user_low; negative means user_low owes user_high.';

create index if not exists idx_balances_friendship
    on public.balances (friendship_id);

-- ---------- Payment Requests ----------
create table if not exists public.payment_requests (
    id uuid primary key default gen_random_uuid(),
    code text not null unique,
    requester_id uuid not null references public.profiles(id) on delete cascade,
    amount numeric(14, 2) not null,
    currency text not null,
    status text not null default 'pending',
    approved_by uuid references public.profiles(id) on delete set null,
    approved_at timestamptz,
    friendship_id uuid references public.friendships(id) on delete set null,
    transaction_id uuid references public.transactions(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint payment_requests_code_not_blank_chk check (length(trim(code)) > 0),
    constraint payment_requests_amount_positive_chk check (amount > 0),
    constraint payment_requests_currency_iso_chk check (
        currency in ('ILS', 'USD', 'EUR', 'RUB')
    ),
    constraint payment_requests_status_chk check (
        status in ('pending', 'processing', 'approved', 'canceled', 'expired')
    ),
    constraint payment_requests_approved_state_chk check (
        (status in ('processing', 'approved') and approved_by is not null)
        or
        (status in ('pending', 'canceled', 'expired') and approved_by is null)
    )
);

comment on table public.payment_requests is 'Deep-link payment approvals created from the In flow.';
comment on column public.payment_requests.code is 'Short code included in Telegram start payload.';

create index if not exists idx_payment_requests_requester_status
    on public.payment_requests (requester_id, status);

create index if not exists idx_payment_requests_status_created_at
    on public.payment_requests (status, created_at desc);

-- ---------- Reminder Log ----------
create table if not exists public.remind_log (
    friendship_id uuid not null references public.friendships(id) on delete cascade,
    currency text not null,
    last_remind_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint remind_log_pkey primary key (friendship_id, currency),
    constraint remind_log_currency_supported_chk check (
        currency in ('ILS', 'USD', 'EUR', 'RUB')
    )
);

comment on table public.remind_log is 'Per-friendship-per-currency reminder cooldown tracker.';
comment on column public.remind_log.last_remind_at is 'Timestamp of the last successfully sent reminder for this friendship+currency pair.';

create index if not exists idx_remind_log_last_remind_at
    on public.remind_log (last_remind_at);

create index if not exists idx_remind_log_friendship_currency_last
    on public.remind_log (friendship_id, currency, last_remind_at desc);

-- ---------- Processed Updates ----------
create table if not exists public.processed_updates (
    update_id bigint primary key,
    processed_at timestamptz not null default now()
);

comment on table public.processed_updates is 'Processed Telegram update IDs for idempotent update handling.';

-- ---------- Updated-at triggers ----------
drop trigger if exists trg_profiles_set_updated_at on public.profiles;
create trigger trg_profiles_set_updated_at
before update on public.profiles
for each row
execute function public.set_updated_at();

drop trigger if exists trg_friendships_set_updated_at on public.friendships;
create trigger trg_friendships_set_updated_at
before update on public.friendships
for each row
execute function public.set_updated_at();

drop trigger if exists trg_invites_set_updated_at on public.invites;
create trigger trg_invites_set_updated_at
before update on public.invites
for each row
execute function public.set_updated_at();

drop trigger if exists trg_transactions_set_updated_at on public.transactions;
create trigger trg_transactions_set_updated_at
before update on public.transactions
for each row
execute function public.set_updated_at();

drop trigger if exists trg_balances_set_updated_at on public.balances;
create trigger trg_balances_set_updated_at
before update on public.balances
for each row
execute function public.set_updated_at();

drop trigger if exists trg_payment_requests_set_updated_at on public.payment_requests;
create trigger trg_payment_requests_set_updated_at
before update on public.payment_requests
for each row
execute function public.set_updated_at();

drop trigger if exists trg_remind_log_set_updated_at on public.remind_log;
create trigger trg_remind_log_set_updated_at
before update on public.remind_log
for each row
execute function public.set_updated_at();

commit;
