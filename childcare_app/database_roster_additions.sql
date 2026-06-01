-- ============================================================
-- ROSTER ENGINE — Additional Database Objects
-- Run AFTER the main schema.sql
-- ============================================================
-- Adds:
--   1. roster_slot_overrides — manual slot-level overrides
--   2. roster_templates      — whole-week saved templates
--   3. Indexes for roster performance
--   4. View: v_roster_week_compliance
-- ============================================================


-- ── 1. Roster slot overrides ──────────────────────────────────
-- Stores manually set children counts for a specific time slot
-- (e.g. a booked excursion changes expected attendance).
CREATE TABLE IF NOT EXISTS roster_slot_overrides (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    centre_id      UUID NOT NULL REFERENCES centres(id) ON DELETE CASCADE,
    room_id        UUID NOT NULL REFERENCES rooms(id)   ON DELETE CASCADE,
    override_date  DATE NOT NULL,
    slot_index     INTEGER NOT NULL CHECK (slot_index BETWEEN 0 AND 55),
    -- slot_index: 0=06:00, 1=06:15, ..., 55=19:45 (15-min intervals)
    children_count INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    created_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_slot_override UNIQUE (room_id, override_date, slot_index)
);

COMMENT ON TABLE  roster_slot_overrides IS
    'Manual per-slot child count overrides. Slot 0=06:00, each step=15 min.';
COMMENT ON COLUMN roster_slot_overrides.slot_index IS
    '0=06:00, 1=06:15, 4=07:00 ... 55=19:45. Total 56 slots per day.';

CREATE INDEX IF NOT EXISTS idx_slot_overrides_room_date
    ON roster_slot_overrides (room_id, override_date);
CREATE INDEX IF NOT EXISTS idx_slot_overrides_centre_date
    ON roster_slot_overrides (centre_id, override_date);


-- ── 2. Roster week templates ──────────────────────────────────
-- A saved "skeleton" roster for a whole week that can be
-- applied to any future roster period with one click.
CREATE TABLE IF NOT EXISTS roster_week_templates (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    centre_id   UUID NOT NULL REFERENCES centres(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Individual shift slots within a week template
-- (day 1=Mon..7=Sun, not tied to actual dates)
CREATE TABLE IF NOT EXISTS roster_week_template_shifts (
    id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    template_id            UUID NOT NULL REFERENCES roster_week_templates(id) ON DELETE CASCADE,
    day_of_week            INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    room_id                UUID REFERENCES rooms(id) ON DELETE SET NULL,
    shift_template_id      UUID REFERENCES shift_templates(id) ON DELETE SET NULL,
    -- Fallback if no shift_template: explicit times
    start_time             TIME NOT NULL,
    end_time               TIME NOT NULL,
    break_duration_minutes INTEGER NOT NULL DEFAULT 0,
    shift_type             shift_type NOT NULL DEFAULT 'standard',
    -- Role requirement (staff are not pre-assigned in templates)
    required_role          user_role,
    requires_diploma       BOOLEAN NOT NULL DEFAULT FALSE,
    notes                  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE roster_week_templates IS
    'Saved whole-week roster skeletons. Apply to a period to pre-fill shifts.';
COMMENT ON COLUMN roster_week_template_shifts.day_of_week IS
    '1=Monday, 2=Tuesday, ..., 7=Sunday.';

CREATE INDEX IF NOT EXISTS idx_week_tpl_shifts_template
    ON roster_week_template_shifts (template_id);
CREATE INDEX IF NOT EXISTS idx_week_templates_centre
    ON roster_week_templates (centre_id);


-- ── 3. Additional performance indexes for roster queries ──────
CREATE INDEX IF NOT EXISTS idx_roster_shifts_centre_date
    ON roster_shifts (centre_id, shift_date)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_roster_shifts_user_date
    ON roster_shifts (user_id, shift_date)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_roster_periods_centre_status
    ON roster_periods (centre_id, status);


-- ── 4. View: v_roster_week_compliance ─────────────────────────
-- Summarises each roster period with conflict counts.
-- Populated by the application after running validation;
-- here we create a base view from available data.
CREATE OR REPLACE VIEW v_roster_week_summary AS
SELECT
    rp.id                       AS period_id,
    rp.centre_id,
    rp.start_date,
    rp.end_date,
    rp.status,
    COUNT(rs.id)                AS total_shifts,
    COUNT(DISTINCT rs.user_id)  AS unique_staff,
    COUNT(DISTINCT rs.room_id)  AS rooms_covered,
    COUNT(DISTINCT rs.shift_date) AS days_with_shifts,
    SUM(EXTRACT(EPOCH FROM (rs.end_time - rs.start_time)) / 3600)
                                AS total_shift_hours,
    AVG(rs.break_duration_minutes) AS avg_break_minutes
FROM roster_periods rp
LEFT JOIN roster_shifts rs
       ON rs.roster_period_id = rp.id
      AND rs.deleted_at IS NULL
GROUP BY rp.id, rp.centre_id, rp.start_date, rp.end_date, rp.status;

COMMENT ON VIEW v_roster_week_summary IS
    'Summary statistics per roster period. Used in roster list and dashboard.';


-- ── 5. Trigger: auto-classify shift type ──────────────────────
-- When a shift is inserted or updated, automatically classify
-- its type as opening/closing/standard/split using the
-- centre's configured opening hours.
CREATE OR REPLACE FUNCTION auto_classify_shift_type()
RETURNS TRIGGER AS $$
DECLARE
    centre_opens  TIME;
    centre_closes TIME;
    s_total       INTEGER;
    day_total     INTEGER;
    mid_slot      INTEGER;
    shift_start_slot INTEGER;
    shift_end_slot   INTEGER;
    open_slot        INTEGER;
    close_slot       INTEGER;
BEGIN
    -- Only auto-classify if shift_type is 'standard' (default)
    -- Leave 'on_call', 'overtime', etc. alone
    IF NEW.shift_type NOT IN ('standard', 'opening', 'closing', 'split') THEN
        RETURN NEW;
    END IF;

    SELECT opens_at, closes_at
    INTO   centre_opens, centre_closes
    FROM   centres WHERE id = NEW.centre_id;

    -- Fall back to defaults if not set
    IF centre_opens  IS NULL THEN centre_opens  := '07:00:00'::TIME; END IF;
    IF centre_closes IS NULL THEN centre_closes := '18:00:00'::TIME; END IF;

    -- Convert to 15-min slot indexes (slot 0 = 06:00)
    open_slot  := EXTRACT(HOUR FROM centre_opens)::INTEGER  * 4
               + EXTRACT(MINUTE FROM centre_opens)::INTEGER  / 15 - 24;
    close_slot := EXTRACT(HOUR FROM centre_closes)::INTEGER * 4
               + EXTRACT(MINUTE FROM centre_closes)::INTEGER / 15 - 24;
    mid_slot   := (open_slot + close_slot) / 2;

    shift_start_slot := EXTRACT(HOUR FROM NEW.start_time)::INTEGER * 4
                      + EXTRACT(MINUTE FROM NEW.start_time)::INTEGER / 15 - 24;
    shift_end_slot   := EXTRACT(HOUR FROM NEW.end_time)::INTEGER   * 4
                      + EXTRACT(MINUTE FROM NEW.end_time)::INTEGER   / 15 - 24;

    IF shift_start_slot <= open_slot AND shift_end_slot >= close_slot THEN
        NEW.shift_type := 'split';
    ELSIF shift_start_slot <= open_slot AND shift_end_slot <= mid_slot THEN
        NEW.shift_type := 'opening';
    ELSIF shift_start_slot >= mid_slot AND shift_end_slot >= close_slot THEN
        NEW.shift_type := 'closing';
    ELSE
        NEW.shift_type := 'standard';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply only when shift_type is being set to one of the auto values
-- (not when explicitly set to overtime, on_call, etc.)
DROP TRIGGER IF EXISTS trg_auto_classify_shift_type ON roster_shifts;
CREATE TRIGGER trg_auto_classify_shift_type
    BEFORE INSERT OR UPDATE OF start_time, end_time, shift_type
    ON roster_shifts
    FOR EACH ROW
    WHEN (NEW.shift_type IN ('standard', 'opening', 'closing', 'split'))
    EXECUTE FUNCTION auto_classify_shift_type();


-- ── 6. RLS on new tables ──────────────────────────────────────
ALTER TABLE roster_slot_overrides    ENABLE ROW LEVEL SECURITY;
ALTER TABLE roster_week_templates    ENABLE ROW LEVEL SECURITY;
ALTER TABLE roster_week_template_shifts ENABLE ROW LEVEL SECURITY;

-- Slot overrides: centre staff can view, managers can modify
CREATE POLICY "centre_staff: view slot overrides"
    ON roster_slot_overrides FOR SELECT
    USING (centre_id = ANY(get_my_centre_ids()));

CREATE POLICY "centre_manager: manage slot overrides"
    ON roster_slot_overrides FOR ALL
    USING (is_manager_at_centre(centre_id));

-- Week templates
CREATE POLICY "centre_staff: view week templates"
    ON roster_week_templates FOR SELECT
    USING (centre_id = ANY(get_my_centre_ids()));

CREATE POLICY "centre_manager: manage week templates"
    ON roster_week_templates FOR ALL
    USING (is_manager_at_centre(centre_id));

CREATE POLICY "centre_staff: view week template shifts"
    ON roster_week_template_shifts FOR SELECT
    USING (
        template_id IN (
            SELECT id FROM roster_week_templates
            WHERE centre_id = ANY(get_my_centre_ids())
        )
    );

CREATE POLICY "centre_manager: manage week template shifts"
    ON roster_week_template_shifts FOR ALL
    USING (
        template_id IN (
            SELECT id FROM roster_week_templates
            WHERE centre_id = ANY(get_my_centre_ids())
              AND is_manager_at_centre(centre_id)
        )
    );


-- ── Grants ────────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE ON roster_slot_overrides       TO authenticated;
GRANT SELECT, INSERT, UPDATE ON roster_week_templates       TO authenticated;
GRANT SELECT, INSERT, UPDATE ON roster_week_template_shifts TO authenticated;
GRANT ALL ON roster_slot_overrides       TO service_role;
GRANT ALL ON roster_week_templates       TO service_role;
GRANT ALL ON roster_week_template_shifts TO service_role;
GRANT SELECT ON v_roster_week_summary    TO authenticated, service_role;
