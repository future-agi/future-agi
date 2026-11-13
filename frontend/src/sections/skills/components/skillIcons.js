import { addCollection, addIcon } from '@iconify/react';
import fileActionIcons from './fileActionIcons';

addCollection(fileActionIcons);

// Keep the shared navigation and identity controls available without an icon
// CDN (including isolated tests and self-hosted/offline installations).
addIcon('solar:menu-dots-bold', { width: 24, height: 24, body: '<g fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></g>' });
addIcon('solar:copy-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><rect x="8" y="8" width="12" height="13" rx="3"/><path stroke-linecap="round" d="M16 8V6a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v7a3 3 0 0 0 3 3h1"/></g>' });
addIcon('solar:info-circle-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path stroke-linecap="round" d="M12 11v6"/></g><circle cx="12" cy="7" r="1" fill="currentColor"/>' });
addIcon('solar:wallet-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linejoin="round" d="M20 8V6a3 3 0 0 0-3-3H6a4 4 0 0 0-4 4v10a4 4 0 0 0 4 4h12a4 4 0 0 0 4-4v-5a4 4 0 0 0-4-4H6a2 2 0 0 1 0-4"/><path d="M22 12h-5a2 2 0 0 0 0 4h5"/></g><circle cx="17" cy="14" r="1" fill="currentColor"/>' });
addIcon('solar:archive-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="5" rx="2"/><path d="M5 8v10a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3V8"/><path stroke-linecap="round" d="M10 12h4"/></g>' });

// Official Solar linear definitions, bundled locally so skill navigation works
// on offline/self-hosted installations. The preferred magic-stick icon is not
// in the application's local registry; use the spec's book-bookmark fallback.
export const SKILLS_NAV_ICON = 'solar:book-bookmark-linear';
addIcon(SKILLS_NAV_ICON, {
  width: 24, height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M4 8C4 5.17157 4 3.75736 4.87868 2.87868C5.75736 2 7.17157 2 10 2H14C16.8284 2 18.2426 2 19.1213 2.87868C20 3.75736 20 5.17157 20 8V16C20 18.8284 20 20.2426 19.1213 21.1213C18.2426 22 16.8284 22 14 22H10C7.17157 22 5.75736 22 4.87868 21.1213C4 20.2426 4 18.8284 4 16V8Z"/><path d="M8 7H16"/><path d="M8 10.5H13"/><path d="M13 16V19.5309C13 19.8065 13 19.9443 12.9051 20C12.8103 20.0557 12.6806 19.9941 12.4211 19.8708L11.1789 19.2808C11.0911 19.2391 11.0472 19.2182 11 19.2182C10.9528 19.2182 10.9089 19.2391 10.8211 19.2808L9.57889 19.8708C9.31943 19.9941 9.18971 20.0557 9.09485 20C9 19.9443 9 19.8065 9 19.5309V16"/><path d="M20 16H7.8981C6.96812 16 6.50314 16 6.12164 16.1022C5.08636 16.3796 4.29937 17.109 4.02197 18.1443"/></g>',
});
addIcon('solar:alt-arrow-right-linear', { width: 24, height: 24, body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5L15 12L9 19"/>' });
addIcon('solar:alt-arrow-down-linear', { width: 24, height: 24, body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 9L12 15L5 9"/>' });

addIcon('solar:inbox-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M3 14l3-10h12l3 10v6H3z"/><path d="M3 14h5l2 3h4l2-3h5"/></g>' });
addIcon('solar:danger-triangle-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path d="M12 3L2 21h20zM12 9v5"/></g><circle cx="12" cy="18" r="1" fill="currentColor"/>' });
addIcon('solar:refresh-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 8a9 9 0 1 0 0 8M20 3v5h-5"/></g>' });
// A permissions boundary is a neutral lock, including in offline workspaces.
addIcon('solar:lock-keyhole-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="10" width="16" height="11" rx="3"/><path stroke-linecap="round" d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/></g>' });

// Task drawers and selected-release indicators must work offline too.
addIcon('solar:close-linear', { width: 24, height: 24, body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5" d="M18 6L6 18M6 6l12 12"/>' });
addIcon('solar:check-circle-linear', { width: 24, height: 24, body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path stroke-linecap="round" stroke-linejoin="round" d="m8 12 3 3 5-6"/></g>' });
