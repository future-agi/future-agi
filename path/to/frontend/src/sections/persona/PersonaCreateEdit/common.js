import { z } from 'zod';

const PersonCreateBaseValidationSchema = z.object({
  simulationType: z.enum(["voice", "text"]),
  name: z.string().trim().min(1, "Name is required"),
  description: z.string().trim().min(1, "Description is required"),
});

export default PersonCreateBaseValidationSchema;