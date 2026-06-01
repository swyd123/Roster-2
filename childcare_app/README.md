# Childcare Platform — Staff Management Module

## What this module covers

| Screen | File | Purpose |
|--------|------|---------|
| Staff List | `pages/staff_list.py` | Browse, search, filter all staff. Export CSV. |
| Add Staff | `pages/staff_add.py` | Create user account + staff profile + centre role in one form |
| Staff Profile | `pages/staff_profile.py` | Tabbed hub: Overview, Qualifications, Availability, Leave, Edit |
| Leave List | `pages/leave_list.py` | All leave requests — approve/decline inline or via full review |
| Leave Review | `pages/leave_review.py` | Full detail view with approve/decline decision form |
| Add Leave | `pages/leave_add.py` | Submit leave request on behalf of a staff member |

## Folder structure

```
childcare_app/
├── app.py                          ← Entry point: streamlit run app.py
├── requirements.txt
├── .env.example                    ← Copy to .env and fill in credentials
├── .gitignore
│
├── utils/
│   ├── supabase_client.py          ← Database connection (cached)
│   ├── staff_queries.py            ← ALL database queries for staff module
│   ├── helpers.py                  ← Formatting, constants, toast helpers
│   └── styles.py                   ← Global CSS (DM Serif + DM Sans)
│
├── components/
│   └── staff_form.py               ← Shared add/edit form (used by add + profile)
│
└── pages/
    ├── staff_list.py               ← Screen 08
    ├── staff_add.py                ← Screen 09
    ├── staff_profile.py            ← Screens 10, 11, 12, 13 (tabbed)
    ├── leave_list.py               ← Screen 14
    ├── leave_review.py             ← Screen 16
    └── leave_add.py                ← Screen 15
```

## Setup

### 1. Install dependencies
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create .env file
Copy `.env.example` to `.env` and fill in:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...
ORGANISATION_ID=your-org-uuid
```

Find these in: Supabase Dashboard → Project Settings → API

### 3. Prepare database
In Supabase SQL Editor, temporarily disable RLS for direct inserts during development:
```sql
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
ALTER TABLE staff_profiles DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_centre_roles DISABLE ROW LEVEL SECURITY;
ALTER TABLE staff_qualifications DISABLE ROW LEVEL SECURITY;
ALTER TABLE staff_availability DISABLE ROW LEVEL SECURITY;
ALTER TABLE leave_requests DISABLE ROW LEVEL SECURITY;
```

### 4. Run
```bash
streamlit run app.py
```

## What each screen does

### Staff List
- Shows all staff with employment type, centre, role, and status
- Search by name/email/employee number
- Filter by employment type and active/inactive status
- Sort by name, start date, or employment type
- Export filtered list as CSV
- Expand any row to see full details, view profile, edit, or remove

### Add Staff
- Single form creates: user account + staff profile + centre role
- Centre and role assignment built in
- Redirects to the new staff member's profile on success

### Staff Profile (5 tabs)
- **Overview** — All personal, employment, and emergency contact details
- **Qualifications** — Full list with expiry status, add/edit/delete/verify, alert banners for expired/expiring items
- **Availability** — Weekly Mon–Sun availability grid with from/until times
- **Leave** — Leave history for this staff member, add leave directly
- **Edit** — Full edit form for all profile fields

### Leave List
- All leave requests with status badges
- Filter by status and leave type, search by staff name
- Quick approve/decline buttons inline, or full review screen
- Export as CSV

### Leave Review
- Full detail view of one request
- Approve or decline with an optional manager's note
- Already-reviewed requests are shown read-only

### Add Leave
- Submit on behalf of any staff member
- Partial day support
- Calculates and displays number of working days

## Design system

| Element | Value |
|---------|-------|
| Heading font | DM Serif Display |
| Body font | DM Sans |
| Primary colour | `#0d1f35` (deep navy) |
| Accent colour | `#1a6b4a` (eucalyptus green) |
| Background | `#ffffff` / `#fafcfe` |
| Border | `#e4edf5` |

## Next modules to build

- Child Register & Attendance
- Room Management & Ratio Monitoring
- Roster Builder
- Break Tracking
- Timesheets & Payroll Export
