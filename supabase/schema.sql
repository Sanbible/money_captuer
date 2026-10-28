-- Money Capture — schema ของ Supabase
-- วิธีใช้: Supabase → SQL Editor → New query → วางทั้งไฟล์ → Run (รันซ้ำได้ ไม่ลบข้อมูล)

create table if not exists public.expenses (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid references auth.users (id) on delete cascade,  -- null = มาจากบอท ยังไม่ผูกบัญชีเว็บ
  discord_id   text,                                               -- Discord user id ของคนส่ง
  source       text not null check (source in ('web', 'discord')),
  date         date not null,
  time         text,                                               -- "HH:MM"
  amount       numeric(12, 2) not null check (amount > 0),
  receiver     text not null default '',
  category     text not null,
  note         text not null default '',
  bank         text not null default '',
  trans_ref    text,                                               -- เลขอ้างอิงจาก QR (null = ไม่มี)
  discord_link text,
  created_at   timestamptz not null default now(),
  check (user_id is not null or discord_id is not null)
);

create index if not exists expenses_user_date on public.expenses (user_id, date);
create index if not exists expenses_discord_date on public.expenses (discord_id, date);

-- กันสลิปซ้ำ "ภายในข้อมูลของคนเดียวกัน" เท่านั้น
create unique index if not exists expenses_user_ref
  on public.expenses (user_id, trans_ref) where user_id is not null and trans_ref is not null;
create unique index if not exists expenses_discord_ref
  on public.expenses (discord_id, trans_ref) where discord_id is not null and trans_ref is not null;

-- เปิด RLS โดยไม่มี policy: browser (anon / authenticated) อ่านเขียนตรงไม่ได้
-- ทุกอย่างต้องผ่าน API ของเรา (service role) ซึ่งกรองตาม user_id เสมอ
alter table public.expenses enable row level security;

-- ผูกรายการจากบอทที่ยังไม่มีเจ้าของเข้าบัญชีเว็บ (ข้ามแถวที่ trans_ref ชนกับของที่บัญชีมีแล้ว)
create or replace function public.claim_discord_expenses(p_user uuid, p_discord text)
returns integer
language sql
security definer
set search_path = public
as $$
  with claimed as (
    update public.expenses e
    set user_id = p_user
    where e.discord_id = p_discord
      and e.user_id is null
      and (e.trans_ref is null or not exists (
        select 1 from public.expenses x where x.user_id = p_user and x.trans_ref = e.trans_ref))
    returning 1
  )
  select count(*)::int from claimed
$$;

revoke execute on function public.claim_discord_expenses(uuid, text) from public, anon, authenticated;
grant execute on function public.claim_discord_expenses(uuid, text) to service_role;
