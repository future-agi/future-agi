import { _mock } from "./_mock";

// ----------------------------------------------------------------------

export const _carouselsMembers = [...Array(6)].map((_, index) => ({
  id: _mock.id(index),
  name: _mock.fullName(index),
  role: _mock.role(index),
  avatarUrl: _mock.image.portrait(index),
}));

// ----------------------------------------------------------------------

export const _faqs = [...Array(8)].map((_, index) => ({
  id: _mock.id(index),
  value: `panel${index + 1}`,
  heading: `Questions ${index + 1}`,
  detail: _mock.description(index),
}));

// ----------------------------------------------------------------------

export const _addressBooks = [...Array(24)].map((_, index) => ({
  id: _mock.id(index),
  primary: index === 0,
  name: _mock.fullName(index),
  email: _mock.email(index + 1),
  fullAddress: _mock.fullAddress(index),
  phoneNumber: _mock.phoneNumber(index),
  company: _mock.companyName(index + 1),
  addressType: index === 0 ? "Home" : "Office",
}));

// ----------------------------------------------------------------------

export const _contacts = [...Array(20)].map((_, index) => {
  const status =
    (index % 2 && "online") ||
    (index % 3 && "offline") ||
    (index % 4 && "alway") ||
    "busy";

  return {
    id: _mock.id(index),
    status,
    role: _mock.role(index),
    email: _mock.email(index),
    name: _mock.fullName(index),
    phoneNumber: _mock.phoneNumber(index),
    lastActivity: _mock.time(index),
    avatarUrl: _mock.image.avatar(index),
    address: _mock.fullAddress(index),
  };
});

// ----------------------------------------------------------------------

export const _notifications = [...Array(9)].map((_, index) => ({
  id: _mock.id(index),
  avatarUrl: [
    _mock.image.avatar(1),
    _mock.image.avatar(2),
    _mock.image.avatar(3),
    _mock.image.avatar(4),
    _mock.image.avatar(5),
    null,
    null,
    null,
    null,
    null,
  ][index],
  type: [
    "friend",
    "project",
    "file",
    "tags",
    "payment",
    "order",
    "chat",
    "mail",
    "delivery",
  ][index],
  category: [
    "Communication",
    "Project UI",
    "File Manager",
    "File Manager",
    "File Manager",
    "Order",
    "Order",
    "Communication",
    "Communication",
  ][index],
  isUnRead: _mock.boolean(index),
  createdAt: _mock.time(index),
  title:
    (index === 0 &&
      `<p><strong>Deja Brady</strong> sent you a friend request</p>`) ||
    (index === 1 &&
      `<p><strong>Jayvon Hull</strong> mentioned you in <strong><a href='#'>Minimal UI</a></strong></p>`) ||
    (index === 2 &&
      `<p><strong>Lainey Davidson</strong> added file to <strong><a href='#'>File Manager</a></strong></p>`) ||
    (index === 3 &&
      `<p><strong>Angelique Morse</strong> added new tags to <strong><a href='#'>File Manager<a/></strong></p>`) ||
    (index === 4 &&
      `<p><strong>Giana Brandt</strong> request a payment of <strong>$200</strong></p>`) ||
    (index === 5 && `<p>Your order is placed waiting for shipping</p>`) ||
    (index === 6 && `<p>Delivery processing your order is being shipped</p>`) ||
    (index === 7 && `<p>You have new message 5 unread messages</p>`) ||
    (index === 8 && `<p>You have new mail`) ||
    "",
}));

// ----------------------------------------------------------------------

export const _mapContact = [
  {
    latlng: [33, 65],
    address: _mock.fullAddress(1),
    phoneNumber: _mock.phoneNumber(1),
  },
  {
    latlng: [-12.5, 18.5],
    address: _mock.fullAddress(2),
    phoneNumber: _mock.phoneNumber(2),
  },
];

// ----------------------------------------------------------------------

export const _socials = [
  {
    value: "facebook",
    name: "FaceBook",
    icon: "eva:facebook-fill",
    color: "#1877F2",
    path: "https://www.facebook.com/caitlyn.kerluke",
  },
  {
    value: "instagram",
    name: "Instagram",
    icon: "ant-design:instagram-filled",
    color: "#E02D69",
    path: "https://www.instagram.com/caitlyn.kerluke",
  },
  {
    value: "linkedin",
    name: "Linkedin",
    icon: "eva:linkedin-fill",
    color: "#007EBB",
    path: "https://www.linkedin.com/caitlyn.kerluke",
  },
  {
    value: "twitter",
    name: "Twitter",
    icon: "eva:twitter-fill",
    color: "#00AAEC",
    path: "https://www.twitter.com/caitlyn.kerluke",
  },
];

// ----------------------------------------------------------------------

export const _homePlans = [...Array(3)].map((_, index) => ({
  license: ["Standard", "Standard Plus", "Extended"][index],
  commons: ["One end products", "12 months updates", "6 months of support"],
  options: [
    "JavaScript version",
    "TypeScript version",
    "Design Resources",
    "Commercial applications",
  ],
  icons: [
    "/assets/icons/platforms/ic_js.svg",
    "/assets/icons/platforms/ic_ts.svg",
    "/assets/icons/platforms/ic_figma.svg",
  ],
}));

// ----------------------------------------------------------------------

export const _pricingPlans = [
  {
    subscription: "basic",
    price: 0,
    caption: "Forever",
    lists: ["3 Prototypes", "3 Boards", "Up To 5 Team Members"],
    labelAction: "Current Plan",
  },
  {
    subscription: "starter",
    price: 4.99,
    caption: "Saving $24 a year",
    lists: [
      "3 Prototypes",
      "3 Boards",
      "Up To 5 Team Members",
      "Advanced Security",
      "Issue Escalation",
    ],
    labelAction: "Choose Starter",
  },
  {
    subscription: "premium",
    price: 9.99,
    caption: "Saving $124 a year",
    lists: [
      "3 Prototypes",
      "3 Boards",
      "Up To 5 Team Members",
      "Advanced Security",
      "Issue Escalation",
      "Issue Development license",
      "Permissions & workflows",
    ],
    labelAction: "Choose Premium",
  },
];

// ----------------------------------------------------------------------

export const _testimonials = [
  {
    name: _mock.fullName(1),
    postedDate: _mock.time(1),
    ratingNumber: _mock.number.rating(1),
    avatarUrl: _mock.image.avatar(1),
    content: `Excellent Work! Thanks a lot!`,
  },
  {
    name: _mock.fullName(2),
    postedDate: _mock.time(2),
    ratingNumber: _mock.number.rating(2),
    avatarUrl: _mock.image.avatar(2),
    content: `It's a very good dashboard and we are really liking the product . We've done some things, like migrate to TS and implementing a react useContext api, to fit our job methodology but the product is one of the best in terms of design and application architecture. The team did a really good job.`,
  },
  {
    name: _mock.fullName(3),
    postedDate: _mock.time(3),
    ratingNumber: _mock.number.rating(3),
    avatarUrl: _mock.image.avatar(3),
    content: `Customer support is realy fast and helpful the desgin of this theme is looks amazing also the code is very clean and readble realy good job !`,
  },
  {
    name: _mock.fullName(4),
    postedDate: _mock.time(4),
    ratingNumber: _mock.number.rating(4),
    avatarUrl: _mock.image.avatar(4),
    content: `Amazing, really good code quality and gives you a lot of examples for implementations.`,
  },
  {
    name: _mock.fullName(5),
    postedDate: _mock.time(5),
    ratingNumber: _mock.number.rating(5),
    avatarUrl: _mock.image.avatar(5),
    content: `Got a few questions after purchasing the product. The owner responded very fast and very helpfull. Overall the code is excellent and works very good. 5/5 stars!`,
  },
  {
    name: _mock.fullName(6),
    postedDate: _mock.time(6),
    ratingNumber: _mock.number.rating(6),
    avatarUrl: _mock.image.avatar(6),
    content: `CEO of Codealy.io here. We’ve built a developer assessment platform that makes sense - tasks are based on git repositories and run in virtual machines. We automate the pain points - storing candidates code, running it and sharing test results with the whole team, remotely. Bought this template as we need to provide an awesome dashboard for our early customers. I am super happy with purchase. The code is just as good as the design. Thanks!`,
  },
];
