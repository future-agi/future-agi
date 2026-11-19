import { addIcon } from '@iconify/react';

// Every Tests drawer closes with the same plain × as the rest of the product (the attempt drawer's
// Solar geometry), registered locally so evidence can always be closed on offline installations.
addIcon('solar:close-linear', {
  width: 24,
  height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M19.0068 5L5.00684 19"/><path d="M19 19L5 5"/></g>',
});

// The shared empty state must remain readable when the icon CDN is unavailable.
addIcon('solar:inbox-linear', {
  width: 24, height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linejoin="round" d="M3 14 6 4h12l3 10v6H3zM3 14h5l2 3h4l2-3h5"/></g>',
});
