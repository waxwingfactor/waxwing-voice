# Alex Project Document: Frontend

## 1. Mission

Alex owns the property manager experience. The dashboard should make daily call review, lead follow-up, property knowledge management, and settings updates clear and efficient.

Primary scope:

- Next.js dashboard
- Page routes
- UI components
- Data states
- Forms
- Filters
- Upload UI
- Frontend API integration
- Responsive layout

## 2. Locked Tools

Use:

- Next.js
- React
- TypeScript
- Tailwind CSS
- Local component system
- lucide-react icons
- Backend APIs from Harsha

Do not introduce:

- Angular, Vue, or Svelte
- Bootstrap or Material UI
- Browser calls to Twilio, LiveKit, Gemini, Whisper, VibeVoice, database, calendar, or email providers
- A second design system
- Dashboard-only fake schemas after backend contracts exist

## 3. Deliverables

### Phase 0

- Frontend app skeleton
- Dashboard route map
- Shared layout concept
- Initial component conventions
- API field request list for Harsha

### Phase 1

- Dashboard shell
- Call history page
- Call detail page with transcript area
- Backend API base URL configuration
- Basic empty, loading, and error states

### Phase 2

- Home Dashboard
- Calls view
- Call Detail view
- Leads view
- Settings skeleton
- Real API integration where available
- Filters for call date, property, intent, and lead status if supported

### Phase 3

- Property Knowledge view
- Document upload UI
- Document processing status
- Structured property facts editor
- Knowledge health indicators
- Re-index action when Harsha supports it

### Phase 4

- Booking details in call and lead views
- Email status in call and lead views
- Email template settings
- Escalation contact settings
- Filters for booked, follow-up sent, and escalated calls

### Phase 5

- UI polish
- Responsive layout pass
- Accessibility pass
- Empty and error state polish
- Basic analytics cards
- Pilot onboarding flow review

## 4. Required Dashboard Views

### Home

- Calls today
- New leads
- Tours booked
- Escalated calls
- Follow-ups sent
- Open action items
- Recent calls

### Calls

- Search
- Filters
- Call status
- Caller phone
- Property
- Intent
- Duration
- Summary preview
- Escalation flag

### Call Detail

- Transcript
- Summary
- Caller info
- Extracted lead fields
- Related booking
- Related emails
- Action items
- Handoff status

### Leads

- Lead list
- Lead status
- Contact information
- Move-in date
- Budget
- Unit preference
- Tour status
- Last call summary

### Property Knowledge

- Structured property facts
- Uploaded documents
- Processing status
- Last indexed timestamp
- Re-index control
- Document delete control, when backend supports it

### Settings

- Business hours
- Escalation contacts
- Calendar connection status
- Email templates
- Voice settings
- Property-specific rules

## 5. UX Guardrails

The dashboard is an operational tool. It should be:

- Dense enough for daily work
- Clear enough for fast review
- Calm and professional
- Consistent across pages
- Responsive on laptop and tablet widths
- Usable with partial or empty data

Avoid:

- Marketing hero pages
- Decorative oversized cards
- Multiple competing visual systems
- Provider credentials shown in the browser
- Hidden failure states
- UI that assumes every call has a lead, booking, or email

## 6. Dependencies

Depends on Harsha for:

- API schemas
- Example responses
- Error responses
- Document status values
- Pagination and filtering behavior
- Upload endpoints
- Settings persistence

Depends on Subbu for:

- Frontend environment variables
- Staging URL
- API base URL
- OAuth redirect URLs
- Deployment process

Depends on Akhil for:

- Transcript format expectations
- Summary and action item fields
- Voice status values that should be readable by managers
- Call failure modes that need UI representation

## 7. Data State Requirements

Each page should handle:

- Loading
- Empty
- Error
- Partial data
- Permission denied, when auth exists
- Processing state for documents
- Failed state for emails, bookings, and document processing

## 8. Definition of Done

A frontend feature is done when:

- It uses approved frontend tools
- It consumes Harsha's API or a documented temporary mock
- It handles loading, empty, and error states
- It avoids direct provider calls
- It is responsive for dashboard use
- It has clear labels and useful actions
- It is visible in staging, if part of the running product

