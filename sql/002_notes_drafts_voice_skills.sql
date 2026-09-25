-- Skinstinct content bot — memory layer (notes / drafts / voice skills)
-- Run once in the Supabase SQL editor, after sql/schema.sql.
--
-- Supersedes skinstinct_content_items for all new code. That table is left
-- in place rather than dropped since it may already hold test data from
-- before this migration; nothing in the app writes to it anymore.

create table if not exists skinstinct_notes (
    id bigint generated always as identity primary key,
    voice_skill text not null,
    text text not null,
    score int,
    score_reason text,
    status text not null default 'pending'
        check (status in ('pending', 'scored_pass', 'scored_reject')),
    created_at timestamptz not null default now()
);

create table if not exists skinstinct_drafts (
    id bigint generated always as identity primary key,
    note_id bigint references skinstinct_notes(id) on delete set null,
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
    search_phrase text,
    used_news_item boolean not null default false,
    news_headline text,
    news_source text,
    news_date text,
    news_link text,
    news_summary text,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'rejected', 'published', 'failed')),
    telegram_message_id text,
    created_at timestamptz not null default now(),
    decided_at timestamptz,
    published_at timestamptz
);

create table if not exists skinstinct_voice_skills (
    id bigint generated always as identity primary key,
    name text unique not null,
    content text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_skinstinct_notes_status on skinstinct_notes(status);
create index if not exists idx_skinstinct_drafts_status on skinstinct_drafts(status);
create index if not exists idx_skinstinct_drafts_note on skinstinct_drafts(note_id);
create index if not exists idx_skinstinct_drafts_voice_platform
    on skinstinct_drafts(voice_skill, platform);
create index if not exists idx_skinstinct_drafts_telegram_message
    on skinstinct_drafts(telegram_message_id);

alter table skinstinct_notes enable row level security;
alter table skinstinct_drafts enable row level security;
alter table skinstinct_voice_skills enable row level security;
