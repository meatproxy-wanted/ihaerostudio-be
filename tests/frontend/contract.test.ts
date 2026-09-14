import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { z } from "zod";

import { easyDocumentSchema } from "@/lib/domain/document";
import { projectSchema } from "@/lib/domain/project";
import {
  publicationSchema,
  publicationSummarySchema,
  publicReadingSchema,
} from "@/lib/domain/publication";
import { reviewCompletionSchema, reviewRunSchema } from "@/lib/domain/review";
import { sourceDocumentSchema } from "@/lib/domain/source";
import { caseStructureSchema } from "@/lib/domain/structure";

// Produced by the backend's real TestClient workflow, never by a FE fixture.
const contractFile = process.env.STUDIO_CONTRACT_FILE;
describe.skipIf(!contractFile)("live backend workflow responses", () => {
  it("passes every corresponding FE zod contract", () => {
    const contract = JSON.parse(readFileSync(contractFile!, "utf8"));
    const schemas = {
      project: projectSchema,
      projects: z.array(projectSchema),
      source: sourceDocumentSchema,
      structure: caseStructureSchema,
      structureResult: z.object({
        structure: caseStructureSchema,
        project: projectSchema,
      }),
      document: easyDocumentSchema,
      documentResult: z.object({
        document: easyDocumentSchema,
        project: projectSchema,
      }),
      run: reviewRunSchema,
      runResult: z.object({ run: reviewRunSchema, project: projectSchema }),
      completionResult: z.object({
        completion: reviewCompletionSchema,
        project: projectSchema,
      }),
      publishResult: z.object({
        publication: publicationSummarySchema,
        project: projectSchema,
      }),
      publications: z.array(publicationSummarySchema),
      publication: publicationSchema,
      publicReading: publicReadingSchema,
    };
    for (const [name, schema] of Object.entries(schemas)) {
      expect(schema.safeParse(contract[name]).success, name).toBe(true);
    }
  });
});
