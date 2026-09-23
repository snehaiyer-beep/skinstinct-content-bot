-- Skinstinct content bot — Supabase schema
-- Run once in the Supabase SQL editor (Project > SQL Editor > New query).
-- Shares the same Supabase project as the movie-matchmaker app; the table
-- name is prefixed so it can't collide with that app's tables.

create table if not exists skinstinct_content_items (
    id bigint generated always as identity primary key,
    voice_skill text not null,
    channel_id text,
    category text not null,
    platform text not null,
    topic text not null,
    subject_line text,
    body text not null,
    placeholders jsonb not null default '[]',
    sources jsonb not null default '[]',
    violations jsonb not null default '[]',
    attempts int not null default 1,
    status text not null default 'draft'
        check (status in ('draft', 'approved', 'published', 'rejected', 'failed')),
    telegram_message_id text,
    created_at timestamptz not null default now(),
    published_at timestamptz
);

create index if not exists idx_skinstinct_content_status on skinstinct_content_items(status);
create index if not exists idx_skinstinct_content_created on skinstinct_content_items(created_at desc);
create index if not exists idx_skinstinct_content_voice_platform
    on skinstinct_content_items(voice_skill, platform);

-- This app talks to Supabase only from the backend using the service role
-- key, which bypasses RLS. Enable RLS with no public policies so the anon/
-- public key (if ever exposed) cannot read or write this table.
alter table skinstinct_content_items enable row level security;
