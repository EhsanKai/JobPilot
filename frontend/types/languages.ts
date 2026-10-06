export type LanguageProficiency =
  | "basic"
  | "conversational"
  | "professional"
  | "fluent"
  | "native";

export interface UserLanguage {
  id: string;
  name: string;
  proficiency: LanguageProficiency;
}

export interface UserLanguageCreateInput {
  name: string;
  proficiency: LanguageProficiency;
}
