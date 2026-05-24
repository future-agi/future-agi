import { countries } from "src/assets/data";

import { _mock } from "./_mock";

// ----------------------------------------------------------------------

export const JOB_DETAILS_TABS = [
  { value: "content", label: "Job Content" },
  { value: "candidates", label: "Candidates" },
];

export const JOB_SKILL_OPTIONS = [
  "UI",
  "UX",
  "Html",
  "JavaScript",
  "TypeScript",
  "Communication",
  "Problem Solving",
  "Leadership",
  "Time Management",
  "Adaptability",
  "Collaboration",
  "Creativity",
  "Critical Thinking",
  "Technical Skills",
  "Customer Service",
  "Project Management",
  "Problem Diagnosis",
];

export const JOB_WORKING_SCHEDULE_OPTIONS = [
  "Monday to Friday",
  "Weekend availability",
  "Day shift",
];

export const JOB_EMPLOYMENT_TYPE_OPTIONS = [
  { value: "Full-time", label: "Full-time" },
  { value: "Part-time", label: "Part-time" },
  { value: "On Demand", label: "On Demand" },
  { value: "Negotiable", label: "Negotiable" },
];

export const JOB_EXPERIENCE_OPTIONS = [
  { value: "No experience", label: "No experience" },
  { value: "1 year exp", label: "1 year exp" },
  { value: "2 year exp", label: "2 year exp" },
  { value: "> 3 year exp", label: "> 3 year exp" },
];

export const JOB_BENEFIT_OPTIONS = [
  { value: "Free parking", label: "Free parking" },
  { value: "Bonus commission", label: "Bonus commission" },
  { value: "Travel", label: "Travel" },
  { value: "Device support", label: "Device support" },
  { value: "Health care", label: "Health care" },
  { value: "Training", label: "Training" },
  { value: "Health Insurance", label: "Health Insurance" },
  { value: "Retirement Plans", label: "Retirement Plans" },
  { value: "Paid Time Off", label: "Paid Time Off" },
  { value: "Flexible Work Schedule", label: "Flexible Work Schedule" },
];

export const JOB_PUBLISH_OPTIONS = [
  {
    value: "published",
    label: "Published",
  },
  {
    value: "draft",
    label: "Draft",
  },
];

export const JOB_SORT_OPTIONS = [
  { value: "latest", label: "Latest" },
  { value: "popular", label: "Popular" },
  { value: "oldest", label: "Oldest" },
];

const CANDIDATES = [...Array(12)].map((_, index) => ({
  id: _mock.id(index),
  role: _mock.role(index),
  name: _mock.fullName(index),
  avatarUrl: _mock.image.avatar(index),
}));

const CONTENT = `
<h6>Job Description</h6>
<br/>

<p>Occaecati est et illo quibusdam accusamus qui. Incidunt aut et molestiae ut facere aut. Est quidem iusto praesentium excepturi harum nihil tenetur facilis. Ut omnis voluptates nihil accusantium doloribus eaque debitis.</p>

<br/>
<br/>

<h6>Key Responsibilities</h6>
<br/>
<ul>
  <li>Working with agency for design drawing detail, quotation and local production.</li>
  <li>Produce window displays, signs, interior displays, floor plans and special promotions displays.</li>
  <li>Change displays to promote new product launches and reflect festive or seasonal themes.</li>
  <li>Planning and executing the open/renovation/ closing store procedure.</li>
  <li>Follow‐up store maintenance procedure and keep updating SKU In &amp; Out.</li>
  <li>Monitor costs and work within budget.</li>
  <li>Liaise with suppliers and source elements.</li>
</ul>

<br/>
<br/>

<h6>Why You'll Love Working Here</h6>
<br/>
<ul>
  <li>Working with agency for design drawing detail, quotation and local production.</li>
  <li>Produce window displays, signs, interior displays, floor plans and special promotions displays.</li>
  <li>Change displays to promote new product launches and reflect festive or seasonal themes.</li>
  <li>Planning and executing the open/renovation/ closing store procedure.</li>
  <li>Follow‐up store maintenance procedure and keep updating SKU In &amp; Out.</li>
  <li>Monitor costs and work within budget.</li>
  <li>Liaise with suppliers and source elements.</li>
</ul>
`;

export const _jobs = [...Array(12)].map((_, index) => {
  const publish = index % 3 ? "published" : "draft";

  const salary = {
    type: (index % 5 && "Custom") || "Hourly",
    price: _mock.number.price(index),
    negotiable: _mock.boolean(index),
  };

  const benefits = JOB_BENEFIT_OPTIONS.slice(0, 3).map(
    (option) => option.label,
  );

  const experience =
    JOB_EXPERIENCE_OPTIONS.map((option) => option.label)[index] ||
    JOB_EXPERIENCE_OPTIONS[1].label;

  const employmentTypes = (index % 2 && ["Part-time"]) ||
    (index % 3 && ["On Demand"]) ||
    (index % 4 && ["Negotiable"]) || ["Full-time"];

  const company = {
    name: _mock.companyName(index),
    logo: _mock.image.company(index),
    phoneNumber: _mock.phoneNumber(index),
    fullAddress: _mock.fullAddress(index),
  };

  const locations = countries.slice(1, index + 2).map((option) => option.label);

  return {
    id: _mock.id(index),
    salary,
    publish,
    company,
    benefits,
    locations,
    experience,
    employmentTypes,
    content: CONTENT,
    candidates: CANDIDATES,
    role: _mock.role(index),
    title: _mock.jobTitle(index),
    createdAt: _mock.time(index),
    expiredDate: _mock.time(index),
    skills: JOB_SKILL_OPTIONS.slice(0, 3),
    totalViews: _mock.number.nativeL(index),
    workingSchedule: JOB_WORKING_SCHEDULE_OPTIONS.slice(0, 2),
  };
});
