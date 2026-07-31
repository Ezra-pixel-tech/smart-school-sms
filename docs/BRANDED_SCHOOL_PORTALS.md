# Branded School Portals

Each active school has an isolated branded login portal. The platform-owner portal remains separate and does not inherit a school's branding.

## URL structure

- Platform Owner: `/owner` and the global `/login?portal=admin`
- School path portal: `/school/<school-slug>/login`
- Optional school subdomain: `https://<school-slug>.<SCHOOL_PORTAL_BASE_DOMAIN>/login`

Path portals work without DNS changes. Subdomains require `SCHOOL_PORTAL_BASE_DOMAIN` plus wildcard DNS and a matching Render custom domain.

## Creating and launching a school

1. Create the school from `/register-school` or the Platform Owner workflow.
2. Print the generated first-administrator login slip.
3. The administrator changes the temporary password on first login.
4. Complete the 14-step setup wizard. Progress resumes at the last saved step.
5. Add the school profile, logo, favicon, optional login background, colours, academic year, and term.
6. The final readiness check activates the portal only after all required items are present.

## Branding

The school's logo, favicon, name, motto, primary colour, secondary colour, accent colour, and login background are selected from the authenticated or URL-resolved school. The same tenant identity brands dashboards, report cards, and payment receipts.

Public branding files are served only through `/school/<school-slug>/branding/<asset>`. Authenticated uploads use school ownership checks, so a user from another school receives a not-found response.

## Tenant isolation

Tenant-owned queries use the authenticated `school_id`. Login candidates are constrained to the school resolved from the path or subdomain. School settings ignore client-supplied school identifiers and update only the authenticated tenant. Cross-school students, staff, parents, communications, invoices, payments, reports, receipts, and uploaded branding are denied by automated tests.

## Render subdomain configuration

1. Set `SCHOOL_PORTAL_BASE_DOMAIN` to the base portal domain, for example `schools.example.com`.
2. Add the matching wildcard custom domain, `*.schools.example.com`, to the Render service.
3. At the DNS provider, create the wildcard record required by Render.
4. Keep `APP_URL` set to the canonical HTTPS application URL.
5. Redeploy, then open `https://<school-slug>.schools.example.com/login`.

Do not include a protocol or wildcard in `SCHOOL_PORTAL_BASE_DOMAIN`. Leave it empty if only path portals are required.
