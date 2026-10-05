export interface CompletenessCategory {
  label: string;
  weight: number;
  score: number;
}

export interface ProfileCompleteness {
  percentage: number;
  categories: CompletenessCategory[];
  message: string;
}
