begin;

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

create index if not exists idx_payment_requests_requester_status
    on public.payment_requests (requester_id, status);

create index if not exists idx_payment_requests_status_created_at
    on public.payment_requests (status, created_at desc);

drop trigger if exists trg_payment_requests_set_updated_at on public.payment_requests;
create trigger trg_payment_requests_set_updated_at
before update on public.payment_requests
for each row
execute function public.set_updated_at();

commit;
