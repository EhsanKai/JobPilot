export interface DataCategory {
  key: string;
  title: string;
  purpose: string;
  retention: string;
  records: Record<string, number>;
}

export interface BrowserStorageItem {
  name: string;
  type: string;
  purpose: string;
  strictly_necessary: boolean;
}

export interface Recipient {
  name: string;
  role: string;
  data: string;
  purpose: string;
  control: string;
}

export interface PrivacyOverview {
  categories: DataCategory[];
  file_storage: string;
  browser_storage: BrowserStorageItem[];
  cookies: BrowserStorageItem[];
  optional_tracking: string[];
  recipients: Recipient[];
  legal_notice: string;
}

export interface PrivacySettings {
  external_ai_processing_allowed: boolean;
  ai_provider: string;
  ai_model: string | null;
  ai_configured: boolean;
  policy_version: string;
  contact_details_pseudonymized: boolean;
}

export interface ConsentRecord {
  category: string;
  policy_version: string;
  granted: boolean;
  decided_at: string;
}

export interface AccountDeletionResult {
  deleted: boolean;
  deleted_records: Record<string, number>;
  deleted_files: number;
  file_cleanup_complete: boolean;
  kept: string[];
}
