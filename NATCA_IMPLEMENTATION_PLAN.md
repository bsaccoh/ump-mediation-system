# NatCA Dashboard Implementation Plan

## Existing UMP Mediation Platform Analysis

### 1. Base Template & Layout (base.html) - REUSED

**Current State:**
- Gradient blue sidebar (#1a237e to #0d47a1) with collapsible functionality
- White topbar with user avatar, notifications, operator selector dropdown
- Bootstrap 5.3.3 and Bootstrap Icons for UI components
- Responsive sidebar collapse (240px → 62px)
- Main content area with margin-left adjustment

**Reuse Strategy:**
- Keep existing sidebar structure and styling
- Add NatCA navigation items under existing Regulatory section (already partially added)
- Use existing topbar pattern for dashboard filters
- Reuse sidebar collapse JavaScript

### 2. Design System (dashboard.css) - REUSED

**Current State:**
- CSS Variables: `--primary: #1378ef`, `--green: #13ae69`, `--orange: #ff9f1c`, `--red: #ec3c47`, `--purple: #8d48dd`
- KPI Cards: White background, 8px border-radius, subtle shadows, flex layout
- Dashboard Cards: White background, 1px border, 8px border-radius
- Tables: 11px font, gray borders (#edf1f6), hover effects
- Doughnut Charts: Custom legend styling, center text overlay
- Badges: Status badges with color coding
- Buttons: Period buttons with active state styling

**Reuse Strategy:**
- Use existing KPI card styling for NatCA KPIs
- Use existing dashboard card styling for charts/tables
- Use existing table styling for operator performance table
- Use existing badge styling for compliance status
- Use existing period button styling for date filters
- Extend CSS with NatCA-specific classes only where needed

### 3. JavaScript (dashboard.js) - REUSED & EXTENDED

**Current State:**
- Chart.js integration with custom color palette: `["#1678ef", "#16ad6a", "#ff852f", "#8749da", "#98a7b9"]`
- `createDoughnut()` function for reusable donut charts
- Filter helpers: `getFilterParams()`, `applyFilters()`
- Async chart data loading from API
- Chart defaults: Inter font, color #536b83

**Reuse Strategy:**
- Reuse `createDoughnut()` for Traffic by Operator chart
- Reuse filter helpers for date/operator filtering
- Reuse Chart.js configuration and color palette
- Create new functions for line charts (Traffic Trend) and bar charts (Service Contribution)
- Create new natca_dashboard.js file extending dashboard.js patterns

### 4. Existing Regulatory Module - EXTENDED

**Current State:**
- Models: TrafficSummary, Tariff, TaxType, TaxRate, RiskAlert, AuditCase, RatedAggregate, RevenueSnapshot
- Views: executive_dashboard, tariff_list, tariff_create, tariff_edit
- URLs: `/regulatory/` namespace with dashboard and tariff endpoints
- Templates: dashboard_executive.html, tariff_list.html, tariff_form.html

**Reuse Strategy:**
- Extend existing regulatory app structure
- Add NatCA-specific views under regulatory app
- Add NatCA-specific services under regulatory/services/
- Reuse existing TrafficSummary model for traffic data
- Reuse existing RiskAlert model for alerts

### 5. Reference Data Models - REUSED

**Current State:**
- **Operator model**: Dynamic operator list (code, name, home_plmn, mcc, mnc)
  - Examples: Orange, Africell, Qcell, SierraTel
- **NumberingPlan**: Number prefix to operator mapping
- **MccMnc**: International roaming identification (MCC/MNC to operator/country)
- **TrunkGroup**: Interconnect partner mapping

**Reuse Strategy:**
- Use Operator model for dynamic operator list in dashboard
- Use NumberingPlan for subscriber analysis
- Use MccMnc for international traffic identification
- Use TrunkGroup for interconnect traffic analysis

### 6. TrafficSummary Model - REUSED

**Current State:**
- Hourly traffic aggregate bucket
- Fields: operator_code, period_start/end, service_type, traffic_type, direction
- Traffic Types: ON_NET, OFF_NET, INTERNATIONAL, ROAMING, INTERCONNECT
- Service Types: VOICE, SMS, DATA
- Metrics: call_count, total_duration_seconds, sms_count, data_volume_bytes
- Indexed on: (operator_code, period_start, service_type), (period_start, traffic_type)

**Reuse Strategy:**
- Use TrafficSummary as primary data source for all traffic KPIs
- Aggregate by day/week/month for trend charts
- Group by operator for traffic by operator chart
- Group by service_type for service contribution chart
- Filter by traffic_type for international/roaming/interconnect metrics

### 7. Authentication & RBAC - REUSED

**Current State:**
- Django built-in authentication
- Custom User model with roles (is_superuser, is_regulator, is_auditor, is_regulatory_admin)
- `@regulator_required` decorator in core/decorators.py
- Permission checks in existing regulatory views

**Reuse Strategy:**
- Reuse existing user model and role checks
- Add NatCA-specific permissions if needed
- Use existing `@regulator_required` decorator
- Check user.is_regulator or user.is_superuser for NatCA dashboard access

### 8. Dashboard Template Pattern (dashboard/index.html) - REUSED

**Current State:**
- Extends base.html
- Custom topbar with tagline, user info, operator selector
- Dashboard heading with title, subtitle, filters
- KPI cards in Bootstrap grid (col-xl col-md-4 col-sm-6)
- Chart cards with card-heading, card-content, chart-large/doughnut-container
- Tables with ump-table styling

**Reuse Strategy:**
- Follow same template structure for NatCA dashboard
- Use same topbar pattern with NatCA-specific filters
- Use same KPI card grid pattern
- Use same chart card pattern
- Use same table pattern for operator performance

---

## Implementation Plan

### STEP 1: Create NatCA Dashboard Service Layer

**File:** `regulatory/services/natca_dashboard_service.py`

**Purpose:** Business logic layer for aggregating dashboard data from TrafficSummary and other models

**Methods:**
- `get_kpis()` - Aggregate KPI data from TrafficSummary
- `get_traffic_trend()` - Monthly traffic trend by service type
- `get_traffic_by_operator()` - Operator traffic distribution
- `get_service_contribution()` - Service type breakdown
- `get_operator_performance()` - Per-operator metrics
- `get_recent_alerts()` - From RiskAlert model
- `get_top_issues()` - Aggregated issue counts
- `get_compliance_status()` - Compliance metrics
- `get_traffic_anomalies()` - Anomaly detection
- `get_upcoming_activities()` - Regulatory calendar

**Data Sources:**
- TrafficSummary (primary)
- RiskAlert (alerts)
- Tariff (compliance)
- Operator (operator list)

### STEP 2: Create NatCA Dashboard View

**File:** `regulatory/views/natca.py`

**Purpose:** Django view for NatCA dashboard

**Implementation:**
- Class-based view: `NatCADashboardView(LoginRequiredMixin, TemplateView)`
- Template: `regulatory/natca/dashboard.html`
- Context data from NatCADashboardService
- Filter parameters: operator, start_date, end_date, period

### STEP 3: Add URL Route

**File:** `regulatory/urls.py`

**Add:**
```python
path('natca/dashboard/', views.natca.NatCADashboardView.as_view(), name='natca_dashboard'),
```

### STEP 4: Create NatCA Dashboard Template

**File:** `templates/regulatory/natca/dashboard.html`

**Structure:**
- Extends base.html
- Custom topbar with NatCA-specific filters
- KPI grid (2 rows of 6 cards each)
- Dashboard grid with charts and tables:
  - Traffic Trend (line chart)
  - Traffic by Operator (donut chart)
  - Service Contribution (bar chart)
  - National Coverage Map (Leaflet map)
  - Recent Alerts (list)
  - Operator Performance Summary (table)
  - Top Issues (table)
  - Compliance Status (progress bars)
  - Traffic Anomalies (table)
  - Upcoming Activities (table)

**Styling:**
- Reuse dashboard.css classes
- Add natca-dashboard.css for NatCA-specific overrides only

### STEP 5: Create NatCA Dashboard CSS

**File:** `static/css/natca-dashboard.css`

**Purpose:** NatCA-specific styling extending dashboard.css

**Contents:**
- KPI grid layout adjustments (6 columns)
- Map container styling
- Compliance progress bar styling
- Any NatCA-specific component tweaks

### STEP 6: Create NatCA Dashboard JavaScript

**File:** `static/js/natca-dashboard.js`

**Purpose:** Chart initialization and interactivity

**Functions:**
- `initTrafficTrend()` - Line chart for monthly traffic
- `initOperatorTraffic()` - Donut chart for operator distribution
- `initServiceContribution()` - Bar chart for service breakdown
- `initMap()` - Leaflet map for national coverage
- Filter handlers for date/operator selection

**Libraries:**
- Reuse Chart.js (already loaded)
- Add Leaflet for map (new dependency)

### STEP 7: Update Sidebar Navigation

**File:** `templates/base.html`

**Changes:**
- Add "Dashboard" link under NatCA submenu (already partially done)
- Update active state logic for NatCA dashboard route

### STEP 8: Add Leaflet Map Library

**File:** `templates/base.html` or dashboard template

**Add:**
```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
```

### STEP 9: Implement RBAC

**File:** `regulatory/views/natca.py`

**Add:**
- `@regulator_required` decorator
- Permission checks for NatCA-specific access

### STEP 10: Performance Optimization

**Strategy:**
- Use TrafficSummary aggregated data (not raw CDRs)
- Add database indexes if needed
- Implement caching for expensive aggregations
- Use Django ORM select_related/prefetch_related

### STEP 11: Empty/Loading/Error States

**Implementation:**
- Loading skeletons during data fetch
- "No data available" states when TrafficSummary is empty
- Error handling for failed queries
- Permission denied state for unauthorized users

### STEP 12: Responsive Testing

**Test on:**
- 1920×1080
- 1600×900
- 1366×768
- Tablet
- Mobile

**Adjustments:**
- KPI grid: 6 cols → 4 cols → 3 cols → 1 col
- Chart grid: 3 cols → 2 cols → 1 col
- Table: horizontal scroll on mobile

---

## Summary

### REUSED Components
1. Base template (base.html) - sidebar, topbar, layout
2. Design system (dashboard.css) - colors, cards, tables, badges
3. Chart.js integration (dashboard.js) - chart library, colors, helpers
4. Regulatory app structure - models, views, URLs
5. TrafficSummary model - primary data source
6. Reference models - Operator, NumberingPlan, MccMnc, TrunkGroup
7. Authentication & RBAC - user model, regulator_required decorator
8. Dashboard template pattern - KPI cards, chart cards, tables

### EXTENDED Components
1. Regulatory app - add NatCA views, services, templates
2. Sidebar navigation - add NatCA dashboard link
3. JavaScript - add NatCA-specific chart functions
4. CSS - add NatCA-specific overrides

### NEW Components
1. NatCADashboardService - business logic layer
2. NatCADashboardView - Django view
3. natca/dashboard.html - dashboard template
4. natca-dashboard.css - NatCA-specific styling
5. natca-dashboard.js - NatCA-specific JavaScript
6. Leaflet map integration - for national coverage map
7. URL route - /regulatory/natca/dashboard/

---

## Next Steps

1. ✅ Inspection complete - proceed to implementation
2. Create NatCADashboardService with placeholder data
3. Create NatCADashboardView
4. Add URL route
5. Create dashboard template with placeholder data
6. Create CSS and JavaScript
7. Connect to real TrafficSummary data
8. Implement drill-down
9. Test responsive behavior
10. Test performance
