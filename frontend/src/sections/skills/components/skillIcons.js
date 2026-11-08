import { addCollection, addIcon } from '@iconify/react';
import fileActionIcons from './fileActionIcons';

addCollection(fileActionIcons);

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
