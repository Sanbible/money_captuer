-- Money Capture — ปาร์ตี้หารค่าใช้จ่ายบนเว็บ (สมาชิก = บัญชีเว็บ, uuid จาก Supabase Auth)
-- วิธีใช้: Supabase → SQL Editor → New query → วางทั้งไฟล์ → Run (รันซ้ำได้ ไม่ลบข้อมูล)
-- เงินเป็นสตางค์ (bigint) ทั้งหมด

-- ---------------------------------------------------------------- ล้างแบบเก่า (บอท Discord) ถ้ายังมีอยู่
do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'parties' and column_name = 'thread_id') then
    drop table if exists public.party_transfers, public.party_expense_shares, public.party_expenses,
      public.party_members, public.parties cascade;
  end if;
end $$;
drop function if exists public.party_join(uuid, text);
drop function if exists public.party_remove_member(uuid, text, text);
drop function if exists public.party_save_expense(uuid, text, bigint, text, text, jsonb);
drop function if exists public.party_delete_expense(uuid, text);
drop function if exists public.party_close(uuid, text);
drop function if exists public.party_toggle_paid(uuid, text);

-- ---------------------------------------------------------------- tables
-- user id = uuid ของบัญชีเว็บ (ไม่ผูก FK กับ auth.users: id มาจาก token ที่ server ตรวจแล้วเท่านั้น)

create table if not exists public.parties (
  id         uuid primary key default gen_random_uuid(),
  code       text not null unique check (code ~ '^[A-Z2-9]{6}$'),
  name       text not null check (char_length(name) between 1 and 100),
  host_id    uuid not null,
  status     text not null default 'open' check (status in ('open', 'closed', 'settled')),
  created_at timestamptz not null default now(),
  closed_at  timestamptz,
  settled_at timestamptz
);

create table if not exists public.party_members (
  party_id     uuid not null references public.parties (id) on delete cascade,
  user_id      uuid not null,
  display_name text not null default '' check (char_length(display_name) <= 100),
  joined_at    timestamptz not null default now(),
  primary key (party_id, user_id)
);

create index if not exists party_members_user on public.party_members (user_id);

create table if not exists public.party_expenses (
  id            uuid primary key default gen_random_uuid(),
  party_id      uuid not null references public.parties (id) on delete cascade,
  amount_satang bigint not null check (amount_satang > 0),
  payer_id      uuid not null,
  created_by    uuid not null,
  note          text not null default '' check (char_length(note) <= 100),
  source        text not null check (source in ('slip', 'cash')),
  trans_ref     text,
  image_sha256  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists party_expenses_party on public.party_expenses (party_id, created_at);
-- สลิปซ้ำในปาร์ตี้เดียวกันบันทึกไม่ได้ (แม้กดพร้อมกัน)
create unique index if not exists party_expenses_ref
  on public.party_expenses (party_id, trans_ref) where trans_ref is not null;
create unique index if not exists party_expenses_image
  on public.party_expenses (party_id, image_sha256) where image_sha256 is not null;

create table if not exists public.party_expense_shares (
  expense_id   uuid not null references public.party_expenses (id) on delete cascade,
  user_id      uuid not null,
  share_satang bigint not null check (share_satang >= 0),
  primary key (expense_id, user_id)
);

create table if not exists public.party_transfers (
  id            uuid primary key default gen_random_uuid(),
  party_id      uuid not null references public.parties (id) on delete cascade,
  seq           integer not null,
  from_id       uuid not null,
  to_id         uuid not null,
  amount_satang bigint not null check (amount_satang > 0),
  paid          boolean not null default false,
  paid_by       uuid,
  paid_at       timestamptz,
  created_at    timestamptz not null default now()
);

create index if not exists party_transfers_party on public.party_transfers (party_id, seq);

-- ยืนยันการโอน: 'slip' = คนโอนแนบสลิปที่ยอดตรง (ระบบตรวจ) ; 'receiver' = คนรับกดยืนยันเอง
alter table public.party_transfers
  add column if not exists paid_method text check (paid_method in ('slip', 'receiver')),
  add column if not exists slip_ref    text,
  add column if not exists slip_sha256 text;
-- สลิปหนึ่งใบใช้ยืนยันได้รายการเดียว (ทุกปาร์ตี้)
create unique index if not exists party_transfers_slip_ref
  on public.party_transfers (slip_ref) where slip_ref is not null;
create unique index if not exists party_transfers_slip_sha
  on public.party_transfers (slip_sha256) where slip_sha256 is not null;

-- ไม่มี policy: browser เข้าไม่ได้ เว็บใช้ service role ผ่าน API ที่ตรวจสิทธิ์แล้ว
alter table public.parties enable row level security;
alter table public.party_members enable row level security;
alter table public.party_expenses enable row level security;
alter table public.party_expense_shares enable row level security;
alter table public.party_transfers enable row level security;

-- ---------------------------------------------------------------- functions
-- ทุกฟังก์ชันที่เปลี่ยนข้อมูลล็อกแถว parties (for update) ก่อนตรวจ → การกดพร้อมกันถูกเรียงคิว

create or replace function public.party_create(p_code text, p_name text, p_host uuid, p_host_name text)
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_id uuid;
begin
  insert into parties (code, name, host_id) values (p_code, p_name, p_host) returning id into v_id;
  insert into party_members (party_id, user_id, display_name) values (v_id, p_host, left(coalesce(p_host_name, ''), 100));
  return v_id;
end $$;

create or replace function public.party_join(p_party uuid, p_user uuid, p_name text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
begin
  select status into v_status from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if v_status <> 'open' then return 'closed'; end if;
  if exists (select 1 from party_members where party_id = p_party and user_id = p_user) then return 'already'; end if;
  if (select count(*) from party_members where party_id = p_party) >= 10 then return 'full'; end if;
  insert into party_members (party_id, user_id, display_name) values (p_party, p_user, left(coalesce(p_name, ''), 100));
  return 'ok';
end $$;

create or replace function public.party_remove_member(p_party uuid, p_member uuid, p_actor uuid)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_host   uuid;
begin
  select status, host_id into v_status, v_host from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> p_member and p_actor <> v_host then return 'forbidden'; end if;
  if p_member = v_host then return 'host'; end if;
  if not exists (select 1 from party_members where party_id = p_party and user_id = p_member) then
    return 'not_member';
  end if;
  if exists (
    select 1 from party_expenses e
    where e.party_id = p_party
      and (e.payer_id = p_member
           or exists (select 1 from party_expense_shares s where s.expense_id = e.id and s.user_id = p_member))
  ) then
    return 'involved';
  end if;
  delete from party_members where party_id = p_party and user_id = p_member;
  return 'ok';
end $$;

-- ตรวจยอด / คนจ่าย / ส่วนแบ่ง (ทุกคนต้องเป็นสมาชิก, ผลรวม = ยอด) — ใช้ภายใน
create or replace function public.party_valid_split(p_party uuid, p_amount bigint, p_payer uuid, p_shares jsonb)
returns boolean language plpgsql stable security definer set search_path = public as $$
begin
  if p_amount is null or p_amount <= 0 or p_shares is null or jsonb_typeof(p_shares) <> 'object'
     or p_shares = '{}'::jsonb then
    return false;
  end if;
  if not exists (select 1 from party_members where party_id = p_party and user_id = p_payer) then return false; end if;
  if exists (
    select 1 from jsonb_each_text(p_shares) s
    where s.value !~ '^\d+$'
       or s.key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or not exists (select 1 from party_members m where m.party_id = p_party and m.user_id::text = s.key)
  ) then
    return false;
  end if;
  return (select sum(value::bigint) from jsonb_each_text(p_shares)) = p_amount;
end $$;

create or replace function public.party_add_expense(
  p_party uuid, p_actor uuid, p_amount bigint, p_payer uuid, p_note text, p_source text,
  p_trans_ref text, p_sha text, p_shares jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_id     uuid;
begin
  select status into v_status from parties where id = p_party for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if v_status <> 'open' then return jsonb_build_object('status', 'closed'); end if;
  if not exists (select 1 from party_members where party_id = p_party and user_id = p_actor) then
    return jsonb_build_object('status', 'not_member');
  end if;
  if not party_valid_split(p_party, p_amount, p_payer, p_shares) then
    return jsonb_build_object('status', 'invalid');
  end if;
  begin
    insert into party_expenses (party_id, amount_satang, payer_id, created_by, note, source, trans_ref, image_sha256)
    values (p_party, p_amount, p_payer, p_actor, left(coalesce(p_note, ''), 100), p_source,
            nullif(p_trans_ref, ''), nullif(p_sha, ''))
    returning id into v_id;
  exception when unique_violation then
    return jsonb_build_object('status', 'duplicate');
  end;
  insert into party_expense_shares (expense_id, user_id, share_satang)
  select v_id, key::uuid, value::bigint from jsonb_each_text(p_shares);
  return jsonb_build_object('status', 'ok', 'id', v_id);
end $$;

create or replace function public.party_update_expense(
  p_expense uuid, p_actor uuid, p_amount bigint, p_payer uuid, p_note text, p_shares jsonb)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party   uuid;
  v_creator uuid;
  v_status  text;
  v_host    uuid;
begin
  select party_id, created_by into v_party, v_creator from party_expenses where id = p_expense;
  if not found then return 'not_found'; end if;
  select status, host_id into v_status, v_host from parties where id = v_party for update;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> v_creator and p_actor <> v_host then return 'forbidden'; end if;
  if not exists (select 1 from party_expenses where id = p_expense) then return 'not_found'; end if;
  if not party_valid_split(v_party, p_amount, p_payer, p_shares) then return 'invalid'; end if;
  update party_expenses
  set amount_satang = p_amount, payer_id = p_payer, note = left(coalesce(p_note, ''), 100), updated_at = now()
  where id = p_expense;
  delete from party_expense_shares where expense_id = p_expense;
  insert into party_expense_shares (expense_id, user_id, share_satang)
  select p_expense, key::uuid, value::bigint from jsonb_each_text(p_shares);
  return 'ok';
end $$;

create or replace function public.party_delete_expense(p_expense uuid, p_actor uuid)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party   uuid;
  v_creator uuid;
  v_status  text;
  v_host    uuid;
begin
  select party_id, created_by into v_party, v_creator from party_expenses where id = p_expense;
  if not found then return 'not_found'; end if;
  select status, host_id into v_status, v_host from parties where id = v_party for update;
  if v_status <> 'open' then return 'closed'; end if;
  if p_actor <> v_creator and p_actor <> v_host then return 'forbidden'; end if;
  delete from party_expenses where id = p_expense;
  return 'ok';
end $$;

create or replace function public.party_close(p_party uuid, p_actor uuid)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_host   uuid;
begin
  select status, host_id into v_status, v_host from parties where id = p_party for update;
  if not found then return 'not_found'; end if;
  if p_actor <> v_host then return 'forbidden'; end if;
  if v_status <> 'open' then return 'already_closed'; end if;
  update parties set status = 'closed', closed_at = now() where id = p_party;
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
    select p_party, t.ord, (t.item ->> 'from_id')::uuid, (t.item ->> 'to_id')::uuid, (t.item ->> 'amount')::bigint
    from jsonb_array_elements(coalesce(p_transfers, '[]'::jsonb)) with ordinality as t (item, ord);
    if coalesce(jsonb_array_length(p_transfers), 0) = 0 then
      update parties set status = 'settled', settled_at = now() where id = p_party;
    end if;
  end if;
  return query select * from party_transfers where party_id = p_party order by seq;
end $$;

-- คนรับกดยืนยันว่าได้เงินแล้ว / กดซ้ำเพื่อยกเลิก (ยืนยันด้วยสลิปแล้วยกเลิกไม่ได้)
-- คืน 'settled' เฉพาะครั้งที่ทำให้ปาร์ตี้เคลียร์ครบ
create or replace function public.party_toggle_paid(p_transfer uuid, p_actor uuid)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party  uuid;
  v_to     uuid;
  v_paid   boolean;
  v_method text;
  v_status text;
begin
  select party_id, to_id into v_party, v_to from party_transfers where id = p_transfer;
  if not found then return 'not_found'; end if;
  select status into v_status from parties where id = v_party for update;
  if v_status <> 'closed' then return 'locked'; end if;
  if p_actor <> v_to then return 'forbidden'; end if;
  select paid, paid_method into v_paid, v_method from party_transfers where id = p_transfer;
  if v_method = 'slip' then return 'verified'; end if;
  update party_transfers
  set paid = not v_paid,
      paid_method = case when v_paid then null else 'receiver' end,
      paid_by = case when v_paid then null else p_actor end,
      paid_at = case when v_paid then null else now() end
  where id = p_transfer;
  if not v_paid and not exists (select 1 from party_transfers where party_id = v_party and not paid) then
    update parties set status = 'settled', settled_at = now() where id = v_party;
    return 'settled';
  end if;
  return case when v_paid then 'unpaid' else 'paid' end;
end $$;

-- คนโอนยืนยันด้วยสลิป: server อ่านยอดจากสลิปแล้วส่งมา ต้องเท่ายอดโอนเป๊ะ ; สลิปเดิมใช้ซ้ำไม่ได้
create or replace function public.party_pay_with_slip(p_transfer uuid, p_actor uuid, p_amount bigint,
  p_trans_ref text, p_sha text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_party  uuid;
  v_from   uuid;
  v_amount bigint;
  v_paid   boolean;
  v_status text;
begin
  select party_id into v_party from party_transfers where id = p_transfer;
  if not found then return 'not_found'; end if;
  select status into v_status from parties where id = v_party for update;
  if v_status <> 'closed' then return 'locked'; end if;
  select from_id, amount_satang, paid into v_from, v_amount, v_paid from party_transfers where id = p_transfer;
  if p_actor <> v_from then return 'forbidden'; end if;
  if v_paid then return 'already_paid'; end if;
  if p_amount is distinct from v_amount then return 'mismatch'; end if;
  begin
    update party_transfers
    set paid = true, paid_method = 'slip', paid_by = p_actor, paid_at = now(),
        slip_ref = nullif(p_trans_ref, ''), slip_sha256 = nullif(p_sha, '')
    where id = p_transfer;
  exception when unique_violation then
    return 'duplicate';
  end;
  if not exists (select 1 from party_transfers where party_id = v_party and not paid) then
    update parties set status = 'settled', settled_at = now() where id = v_party;
    return 'settled';
  end if;
  return 'paid';
end $$;

do $$
declare
  f text;
begin
  foreach f in array array[
    'party_create(text, text, uuid, text)', 'party_join(uuid, uuid, text)',
    'party_remove_member(uuid, uuid, uuid)', 'party_valid_split(uuid, bigint, uuid, jsonb)',
    'party_add_expense(uuid, uuid, bigint, uuid, text, text, text, text, jsonb)',
    'party_update_expense(uuid, uuid, bigint, uuid, text, jsonb)', 'party_delete_expense(uuid, uuid)',
    'party_close(uuid, uuid)', 'party_create_transfers(uuid, jsonb)', 'party_toggle_paid(uuid, uuid)',
    'party_pay_with_slip(uuid, uuid, bigint, text, text)'
  ] loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;
