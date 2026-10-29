-- Money Capture — ปาร์ตี้หารค่าใช้จ่าย (บอท Discord)
-- วิธีใช้: Supabase → SQL Editor → New query → วางทั้งไฟล์ → Run (รันซ้ำได้ ไม่ลบข้อมูล)
-- เงินเป็นสตางค์ (bigint) ทั้งหมด ; Discord id เก็บเป็น text

create table if not exists public.parties (
  id                 uuid primary key default gen_random_uuid(),
  code               text not null unique check (code ~ '^[A-Z2-9]{6}$'),
  name               text not null check (char_length(name) between 1 and 100),
  host_id            text not null,
  guild_id           text not null,
  channel_id         text not null,                -- ช่องหลักที่มีข้อความเชิญ
  thread_id          text not null unique,         -- private thread ของปาร์ตี้
  invite_message_id  text,
  summary_message_id text,                         -- ข้อความสรุปตอนปิด
  status             text not null default 'open' check (status in ('open', 'closed', 'settled')),
  created_at         timestamptz not null default now(),
  closed_at          timestamptz,
  settled_at         timestamptz
);

create table if not exists public.party_members (
  party_id   uuid not null references public.parties (id) on delete cascade,
  discord_id text not null,
  joined_at  timestamptz not null default now(),
  primary key (party_id, discord_id)
);

create table if not exists public.party_expenses (
  id                 uuid primary key default gen_random_uuid(),
  party_id           uuid not null references public.parties (id) on delete cascade,
  status             text not null default 'draft' check (status in ('draft', 'confirmed')),
  amount_satang      bigint check (amount_satang is null or amount_satang > 0),
  payer_id           text not null,
  created_by         text not null,
  participants       text[] not null default '{}',  -- ใช้ตอนเป็น draft
  note               text not null default '' check (char_length(note) <= 100),
  source             text not null check (source in ('slip', 'cash')),
  trans_ref          text,
  image_sha256       text,
  slip_message_url   text,
  summary_message_id text,                          -- ข้อความ "พบสลิป" / สรุปรายการใน thread
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  check (status = 'draft' or amount_satang is not null)
);

create index if not exists party_expenses_party on public.party_expenses (party_id, status);
-- สลิปซ้ำในปาร์ตี้เดียวกันบันทึกไม่ได้ (แม้กดยืนยันพร้อมกัน)
create unique index if not exists party_expenses_ref
  on public.party_expenses (party_id, trans_ref) where status = 'confirmed' and trans_ref is not null;
create unique index if not exists party_expenses_image
  on public.party_expenses (party_id, image_sha256) where status = 'confirmed' and image_sha256 is not null;

create table if not exists public.party_expense_shares (
  expense_id   uuid not null references public.party_expenses (id) on delete cascade,
  discord_id   text not null,
  share_satang bigint not null check (share_satang >= 0),
  primary key (expense_id, discord_id)
);

create table if not exists public.party_transfers (
  id            uuid primary key default gen_random_uuid(),
  party_id      uuid not null references public.parties (id) on delete cascade,
  seq           integer not null,                  -- ลำดับในสรุป
  from_id       text not null,
  to_id         text not null,
  amount_satang bigint not null check (amount_satang > 0),
  paid          boolean not null default false,
  paid_by       text,
  paid_at       timestamptz,
  created_at    timestamptz not null default now()
);

create index if not exists party_transfers_party on public.party_transfers (party_id, seq);

-- ไม่มี policy: browser เข้าไม่ได้ บอทใช้ service role
alter table public.parties enable row level security;
alter table public.party_members enable row level security;
alter table public.party_expenses enable row level security;
alter table public.party_expense_shares enable row level security;
alter table public.party_transfers enable row level security;

-- ---------------------------------------------------------------- functions
-- ทุกฟังก์ชันล็อกแถว parties (for update) ก่อนตรวจ → การกดพร้อมกันถูกเรียงคิว

create or replace function public.party_join(p_party uuid, p_member text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
begin
  select status into v_status from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if v_status <> 'open' then return 'closed'; end if;
  if exists (select 1 from party_members where party_id = p_party and discord_id = p_member) then
    return 'already';
  end if;
  if (select count(*) from party_members where party_id = p_party) >= 10 then return 'full'; end if;
  insert into party_members (party_id, discord_id) values (p_party, p_member);
  return 'ok';
end $$;

create or replace function public.party_remove_member(p_party uuid, p_member text, p_actor text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_host   text;
begin
  select status, host_id into v_status, v_host from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> p_member and p_actor <> v_host then return 'forbidden'; end if;
  if p_member = v_host then return 'host'; end if;
  if not exists (select 1 from party_members where party_id = p_party and discord_id = p_member) then
    return 'not_member';
  end if;
  if exists (
    select 1 from party_expenses e
    where e.party_id = p_party and e.status = 'confirmed'
      and (e.payer_id = p_member
           or exists (select 1 from party_expense_shares s where s.expense_id = e.id and s.discord_id = p_member))
  ) then
    return 'involved';
  end if;
  delete from party_members where party_id = p_party and discord_id = p_member;
  return 'ok';
end $$;

create or replace function public.party_save_expense(
  p_expense uuid, p_actor text, p_amount bigint, p_payer text, p_note text, p_shares jsonb)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party   uuid;
  v_creator text;
  v_status  text;
  v_host    text;
begin
  select party_id, created_by into v_party, v_creator from party_expenses where id = p_expense;
  if not found then return 'not_found'; end if;
  select status, host_id into v_status, v_host from parties where id = v_party for update;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> v_creator and p_actor <> v_host then return 'forbidden'; end if;
  if not exists (select 1 from party_expenses where id = p_expense) then return 'not_found'; end if;

  if p_amount is null or p_amount <= 0 or p_shares is null or jsonb_typeof(p_shares) <> 'object'
     or p_shares = '{}'::jsonb then
    return 'invalid';
  end if;
  if not exists (select 1 from party_members where party_id = v_party and discord_id = p_payer) then
    return 'invalid';
  end if;
  if exists (
    select 1 from jsonb_each_text(p_shares) s
    where s.value !~ '^\d+$'
       or not exists (select 1 from party_members m where m.party_id = v_party and m.discord_id = s.key)
  ) then
    return 'invalid';
  end if;
  if (select sum(value::bigint) from jsonb_each_text(p_shares)) <> p_amount then return 'invalid'; end if;

  begin
    update party_expenses
    set status = 'confirmed', amount_satang = p_amount, payer_id = p_payer, note = coalesce(p_note, ''),
        participants = array(select jsonb_object_keys(p_shares)), updated_at = now()
    where id = p_expense;
  exception when unique_violation then
    return 'duplicate';
  end;
  delete from party_expense_shares where expense_id = p_expense;
  insert into party_expense_shares (expense_id, discord_id, share_satang)
  select p_expense, key, value::bigint from jsonb_each_text(p_shares);
  return 'ok';
end $$;

create or replace function public.party_delete_expense(p_expense uuid, p_actor text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party   uuid;
  v_creator text;
  v_status  text;
  v_host    text;
begin
  select party_id, created_by into v_party, v_creator from party_expenses where id = p_expense;
  if not found then return 'not_found'; end if;
  select status, host_id into v_status, v_host from parties where id = v_party for update;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> v_creator and p_actor <> v_host then return 'forbidden'; end if;
  delete from party_expenses where id = p_expense;
  return 'ok';
end $$;

create or replace function public.party_close(p_party uuid, p_actor text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_host   text;
begin
  select status, host_id into v_status, v_host from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if p_actor <> v_host then return 'forbidden'; end if;
  if v_status <> 'open' then return 'already_closed'; end if;
  update parties set status = 'closed', closed_at = now() where id = p_party;
  delete from party_expenses where party_id = p_party and status = 'draft';
  return 'ok';
end $$;

-- สร้างรายการโอนครั้งเดียว (เรียกซ้ำได้ คืนชุดเดิม) ; ไม่มีรายการโอน → settled ทันที
create or replace function public.party_create_transfers(p_party uuid, p_transfers jsonb)
returns setof public.party_transfers language plpgsql security definer set search_path = public as $$
declare
  v_status text;
begin
  select status into v_status from parties where id = p_party for update;
  if v_status = 'closed' and not exists (select 1 from party_transfers where party_id = p_party) then
    insert into party_transfers (party_id, seq, from_id, to_id, amount_satang)
    select p_party, t.ord, t.item ->> 'from_id', t.item ->> 'to_id', (t.item ->> 'amount')::bigint
    from jsonb_array_elements(coalesce(p_transfers, '[]'::jsonb)) with ordinality as t (item, ord);
    if coalesce(jsonb_array_length(p_transfers), 0) = 0 then
      update parties set status = 'settled', settled_at = now() where id = p_party;
    end if;
  end if;
  return query select * from party_transfers where party_id = p_party order by seq;
end $$;

-- สลับสถานะ "โอนแล้ว" (คนโอนหรือคนรับ) ; คืน 'settled' เฉพาะครั้งที่ทำให้ปาร์ตี้เคลียร์ครบ
create or replace function public.party_toggle_paid(p_transfer uuid, p_actor text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party  uuid;
  v_from   text;
  v_to     text;
  v_paid   boolean;
  v_status text;
begin
  select party_id, from_id, to_id into v_party, v_from, v_to from party_transfers where id = p_transfer;
  if not found then return 'not_found'; end if;
  select status into v_status from parties where id = v_party for update;
  if v_status <> 'closed' then return 'locked'; end if;
  if p_actor <> v_from and p_actor <> v_to then return 'forbidden'; end if;
  select paid into v_paid from party_transfers where id = p_transfer;
  update party_transfers
  set paid = not v_paid,
      paid_by = case when v_paid then null else p_actor end,
      paid_at = case when v_paid then null else now() end
  where id = p_transfer;
  if not v_paid and not exists (select 1 from party_transfers where party_id = v_party and not paid) then
    update parties set status = 'settled', settled_at = now() where id = v_party;
    return 'settled';
  end if;
  return case when v_paid then 'unpaid' else 'paid' end;
end $$;

do $$
declare
  f text;
begin
  foreach f in array array[
    'party_join(uuid, text)', 'party_remove_member(uuid, text, text)',
    'party_save_expense(uuid, text, bigint, text, text, jsonb)', 'party_delete_expense(uuid, text)',
    'party_close(uuid, text)', 'party_create_transfers(uuid, jsonb)', 'party_toggle_paid(uuid, text)'
  ] loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;
