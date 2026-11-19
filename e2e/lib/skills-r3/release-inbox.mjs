// The desktop inbox keeps the request name in the row and its action in the last cell.
export const openRequestButton = (page, name) => page.getByRole('table', {name:'Assigned release requests'})
  .getByRole('row').filter({hasText:name}).getByRole('button', {name:'Open request', exact:true});
