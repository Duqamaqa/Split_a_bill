-- Multi-currency migration for Owee debt bot
-- Supported currencies: ILS, USD, EUR, RUB
-- Sign convention remains unchanged:
--   balances.net_amount > 0 => user_high owes user_low
--   balances.net_amount < 0 => user_low owes user_high

begin;

-- ---------- Profiles ----------
alter table if exists public.profiles
    add column if not exists default_currency text;

update public.profiles
set default_currency = upper(coalesce(nullif(trim(default_currency), ''), 'ILS'));

update public.profiles
set default_currency = 'ILS'
where default_currency not in ('ILS', 'USD', 'EUR', 'RUB');

alter table if exists public.profiles
    alter column default_currency set default 'ILS';

alter table if exists public.profiles
    alter column default_currency set not null;

do $$
begin
    if exists (
        select 1
        from pg_constraint
        where conrelid = 'public.profiles'::regclass
          and conname = 'profiles_default_currency_supported_chk'
    ) then
        alter table public.profiles
            drop constraint profiles_default_currency_supported_chk;
    end if;

    alter table public.profiles
        add constraint profiles_default_currency_supported_chk
        check (default_currency in ('ILS', 'USD', 'EUR', 'RUB'));
end
$$;

-- ---------- Transactions ----------
alter table if exists public.transactions
    add column if not exists currency text;

update public.transactions
set currency = upper(coalesce(nullif(trim(currency), ''), 'ILS'));

update public.transactions
set currency = 'ILS'
where currency not in ('ILS', 'USD', 'EUR', 'RUB');

alter table if exists public.transactions
    alter column currency set not null;

do $$
begin
    if exists (
        select 1
        from pg_constraint
        where conrelid = 'public.transactions'::regclass
          and conname = 'transactions_currency_supported_chk'
    ) then
        alter table public.transactions
            drop constraint transactions_currency_supported_chk;
    end if;

    alter table public.transactions
        add constraint transactions_currency_supported_chk
        check (currency in ('ILS', 'USD', 'EUR', 'RUB'));
end
$$;

-- ---------- Balances ----------
alter table if exists public.balances
    add column if not exists currency text;

update public.balances
set currency = upper(coalesce(nullif(trim(currency), ''), 'ILS'));

update public.balances
set currency = 'ILS'
where currency not in ('ILS', 'USD', 'EUR', 'RUB');

alter table if exists public.balances
    alter column currency set not null;

alter table if exists public.balances
    alter column net_amount set default 0;

alter table if exists public.balances
    alter column net_amount set not null;

do $$
declare
    pk_name text;
    pk_cols text[];
begin
    select c.conname, array_agg(a.attname order by u.ordinality)
    into pk_name, pk_cols
    from pg_constraint c
    join unnest(c.conkey) with ordinality as u(attnum, ordinality) on true
    join pg_attribute a
      on a.attrelid = c.conrelid
     and a.attnum = u.attnum
    where c.conrelid = 'public.balances'::regclass
      and c.contype = 'p'
    group by c.conname;

    if pk_name is not null and pk_cols = array['friendship_id'] then
        execute format('alter table public.balances drop constraint %I', pk_name);
        alter table public.balances
            add constraint balances_pkey primary key (friendship_id, currency);
    end if;
end
$$;

create unique index if not exists idx_balances_friendship_currency_unique
    on public.balances (friendship_id, currency);

do $$
begin
    if exists (
        select 1
        from pg_constraint
        where conrelid = 'public.balances'::regclass
          and conname = 'balances_currency_supported_chk'
    ) then
        alter table public.balances
            drop constraint balances_currency_supported_chk;
    end if;

    alter table public.balances
        add constraint balances_currency_supported_chk
        check (currency in ('ILS', 'USD', 'EUR', 'RUB'));
end
$$;

-- ---------- Reminder cooldown (friendship + currency) ----------
create table if not exists public.remind_log (
    friendship_id uuid not null references public.friendships(id) on delete cascade,
    currency text not null default 'ILS',
    last_remind_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint remind_log_pkey primary key (friendship_id, currency),
    constraint remind_log_currency_supported_chk check (currency in ('ILS', 'USD', 'EUR', 'RUB'))
);

alter table if exists public.remind_log
    add column if not exists currency text;

update public.remind_log
set currency = upper(coalesce(nullif(trim(currency), ''), 'ILS'));

update public.remind_log
set currency = 'ILS'
where currency not in ('ILS', 'USD', 'EUR', 'RUB');

alter table if exists public.remind_log
    alter column currency set not null;

do $$
declare
    constraint_name text;
    pk_name text;
    pk_cols text[];
begin
    -- Remove old unique(friendship_id) constraints.
    for constraint_name in
        select c.conname
        from pg_constraint c
        where c.conrelid = 'public.remind_log'::regclass
          and c.contype = 'u'
          and c.conkey = array[
              (
                  select a.attnum
                  from pg_attribute a
                  where a.attrelid = 'public.remind_log'::regclass
                    and a.attname = 'friendship_id'
                    and not a.attisdropped
                  limit 1
              )
          ]
    loop
        execute format('alter table public.remind_log drop constraint %I', constraint_name);
    end loop;

    -- Replace non-composite PKs.
    select c.conname, array_agg(a.attname order by u.ordinality)
    into pk_name, pk_cols
    from pg_constraint c
    join unnest(c.conkey) with ordinality as u(attnum, ordinality) on true
    join pg_attribute a
      on a.attrelid = c.conrelid
     and a.attnum = u.attnum
    where c.conrelid = 'public.remind_log'::regclass
      and c.contype = 'p'
    group by c.conname;

    if pk_name is not null and pk_cols <> array['friendship_id', 'currency'] then
        execute format('alter table public.remind_log drop constraint %I', pk_name);
    end if;
end
$$;

alter table if exists public.remind_log
    drop column if exists id;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.remind_log'::regclass
          and contype = 'p'
    ) then
        alter table public.remind_log
            add constraint remind_log_pkey primary key (friendship_id, currency);
    end if;
end
$$;

do $$
begin
    if exists (
        select 1
        from pg_constraint
        where conrelid = 'public.remind_log'::regclass
          and conname = 'remind_log_currency_supported_chk'
    ) then
        alter table public.remind_log
            drop constraint remind_log_currency_supported_chk;
    end if;

    alter table public.remind_log
        add constraint remind_log_currency_supported_chk
        check (currency in ('ILS', 'USD', 'EUR', 'RUB'));
end
$$;

create index if not exists idx_remind_log_friendship_currency_last
    on public.remind_log (friendship_id, currency, last_remind_at desc);

commit;
