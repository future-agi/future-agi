import { addIcon } from '@iconify/react';

// Keep the evidence drawer's close control visible on offline installations.
addIcon('solar:close-circle-linear', {
  width: 24,
  height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path stroke-linecap="round" d="m9 9 6 6m0-6-6 6"/></g>',
});

// The shared empty state must remain readable when the icon CDN is unavailable.
addIcon('solar:inbox-linear', {
  width: 24, height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linejoin="round" d="M3 14 6 4h12l3 10v6H3zM3 14h5l2 3h4l2-3h5"/></g>',
});
