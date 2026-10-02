import { add, subDays } from "date-fns";

import { _mock } from "./_mock";
import { _addressBooks } from "./_others";

// ----------------------------------------------------------------------

export const INVOICE_STATUS_OPTIONS = [
  { value: "paid", label: "Paid" },
  { value: "pending", label: "Pending" },
  { value: "overdue", label: "Overdue" },
  { value: "draft", label: "Draft" },
];

export const INVOICE_SERVICE_OPTIONS = [...Array(8)].map((_, index) => ({
  id: _mock.id(index),
  name: _mock.role(index),
  price: _mock.number.price(index),
}));

const ITEMS = [...Array(3)].map((__, index) => {
  const total =
    INVOICE_SERVICE_OPTIONS[index].price * _mock.number.nativeS(index);

  return {
    id: _mock.id(index),
    total,
    title: _mock.productName(index),
    description: _mock.sentence(index),
    price: INVOICE_SERVICE_OPTIONS[index].price,
    service: INVOICE_SERVICE_OPTIONS[index].name,
    quantity: _mock.number.nativeS(index),
  };
});

export const _invoices = [...Array(20)].map((_, index) => {
  const taxes = _mock.number.price(index + 1);

  const discount = _mock.number.price(index + 2);

  const shipping = _mock.number.price(index + 3);

  const subTotal = ITEMS.reduce(
    (accumulator, item) => accumulator + item.price * item.quantity,
    0,
  );

  const totalAmount = subTotal - shipping - discount + taxes;

  const status =
    (index % 2 && "paid") ||
    (index % 3 && "pending") ||
    (index % 4 && "overdue") ||
    "draft";

  return {
    id: _mock.id(index),
    taxes,
    status,
    discount,
    shipping,
    subTotal,
    totalAmount,
    items: ITEMS,
    invoiceNumber: `INV-199${index}`,
    invoiceFrom: _addressBooks[index],
    invoiceTo: _addressBooks[index + 1],
    sent: _mock.number.nativeS(index),
    createDate: subDays(new Date(), index),
    dueDate: add(new Date(), { days: index + 15, hours: index }),
  };
});
