//! The vocabulary and its English-side lookup table.

use rust_stemmers::{Algorithm, Stemmer};
use std::collections::{HashMap, HashSet};

pub struct Entry {
    pub id: String,
    pub spanish: String,
    pub english: String,
    pub kind: String,
}

pub struct Index {
    pub entries: Vec<Entry>,
    /// Normalised English key (stemmed words joined by a space) to entries.
    pub map: HashMap<String, Vec<usize>>,
    /// Normalised key to (entry, raw lowercase key), for the inflection guard.
    pub forms: HashMap<String, Vec<(usize, Form)>>,
    irregular: HashMap<&'static str, &'static str>,
    irregular_nouns: HashMap<&'static str, &'static str>,
    /// Words in the longest key, which bounds the phrase search.
    pub max_words: usize,
    stemmer: Stemmer,
}

/// Words that carry English grammar, not content. Substituting them would turn
/// the sentence into Spanish, so a single one of them never matches on its own.
const STOP: &[&str] = &[
    "it", "i", "we", "you", "they", "he", "she", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "this", "that", "these", "those", "the", "a", "an", "to",
    "be", "am", "is", "are", "was", "were", "been", "being", "do", "does", "did", "done", "doing",
    "have", "has", "had", "having", "can", "could", "will", "would", "shall", "should", "may",
    "might", "must", "of", "in", "on", "at", "for", "with", "from", "by", "as", "up", "not",
];

const IRREGULAR: &[(&str, &str)] = &[
    ("am", "be"), ("is", "be"), ("are", "be"), ("was", "be"), ("were", "be"), ("been", "be"),
    ("being", "be"), ("did", "do"), ("does", "do"), ("done", "do"), ("doing", "do"),
    ("had", "have"), ("has", "have"), ("having", "have"), ("went", "go"), ("gone", "go"),
    ("goes", "go"), ("going", "go"), ("saw", "see"), ("seen", "see"), ("made", "make"),
    ("took", "take"), ("taken", "take"), ("gave", "give"), ("given", "give"),
    ("found", "find"), ("thought", "think"), ("came", "come"), ("got", "get"),
    ("gotten", "get"), ("said", "say"), ("told", "tell"), ("wrote", "write"),
    ("written", "write"), ("ate", "eat"), ("eaten", "eat"), ("drank", "drink"),
    ("drunk", "drink"), ("ran", "run"), ("began", "begin"), ("begun", "begin"),
    ("built", "build"), ("bought", "buy"), ("brought", "bring"), ("left", "leave"),
    ("kept", "keep"), ("lost", "lose"), ("met", "meet"), ("paid", "pay"), ("sent", "send"),
    ("spoke", "speak"), ("spoken", "speak"), ("spent", "spend"), ("stood", "stand"),
    ("understood", "understand"), ("wore", "wear"), ("worn", "wear"), ("won", "win"),
    ("taught", "teach"), ("sat", "sit"), ("slept", "sleep"), ("heard", "hear"),
    ("felt", "feel"), ("held", "hold"), ("knew", "know"), ("known", "know"),
    ("chose", "choose"), ("chosen", "choose"), ("drove", "drive"), ("driven", "drive"),
    ("flew", "fly"), ("flown", "fly"), ("forgot", "forget"), ("forgotten", "forget"),
    ("grew", "grow"), ("grown", "grow"), ("hid", "hide"), ("hidden", "hide"),
    ("led", "lead"), ("lay", "lie"), ("lain", "lie"), ("rose", "rise"), ("risen", "rise"),
    ("sold", "sell"), ("shook", "shake"), ("shown", "show"), ("sang", "sing"),
    ("sung", "sing"), ("swam", "swim"), ("threw", "throw"), ("thrown", "throw"),
    ("woke", "wake"), ("woken", "wake"), ("caught", "catch"),
];

/// Irregular noun plurals. These only map to noun entries.
const IRREGULAR_NOUNS: &[(&str, &str)] = &[
    ("children", "child"), ("men", "man"), ("women", "woman"), ("people", "person"),
    ("feet", "foot"), ("teeth", "tooth"),
];

/// Inflections a key word may take: `-s`/`-es`/`-ies`, `-ed`/`-d`/`-ied`,
/// `-ing`, an irregular verb form, an irregular noun plural.
pub const PLURAL: u8 = 1;
pub const PAST: u8 = 2;
pub const ING: u8 = 4;
pub const IRR_VERB: u8 = 8;
pub const IRR_NOUN: u8 = 16;
pub const VERB: u8 = PLURAL | PAST | ING | IRR_VERB;
pub const NOUN: u8 = PLURAL | IRR_NOUN;

/// One English spelling of an entry, with what each word may inflect into.
#[derive(Clone, Debug)]
pub struct Form {
    pub words: Vec<String>,
    pub masks: Vec<u8>,
}

pub fn is_stop(word: &str) -> bool {
    STOP.contains(&word)
}

impl Index {
    pub fn parse(raw: &str) -> Index {
        let mut index = Index {
            entries: Vec::new(),
            map: HashMap::new(),
            forms: HashMap::new(),
            irregular: IRREGULAR.iter().copied().collect(),
            irregular_nouns: IRREGULAR_NOUNS.iter().copied().collect(),
            max_words: 1,
            stemmer: Stemmer::create(Algorithm::English),
        };
        let mut kind = String::new();
        for line in raw.lines() {
            if let Some(rest) = line.strip_prefix("## ") {
                kind = rest.split_whitespace().next().unwrap_or("").to_string();
                continue;
            }
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let parts: Vec<&str> = line.split(" | ").map(str::trim).collect();
            if parts.len() != 3 || parts.iter().any(|p| p.is_empty()) {
                continue;
            }
            let at = index.entries.len();
            index.entries.push(Entry {
                id: parts[0].to_string(),
                spanish: parts[1].to_string(),
                english: parts[2].to_string(),
                kind: kind.clone(),
            });
            let mut seen = HashSet::new();
            for (key, masks) in english_keys(parts[2], &kind) {
                let words: Vec<&str> = key.split(' ').collect();
                if words.len() == 1 && is_stop(words[0]) {
                    continue;
                }
                let normalised = words
                    .iter()
                    .map(|w| index.norm(w))
                    .collect::<Vec<_>>()
                    .join(" ");
                if seen.insert(normalised.clone()) {
                    index.max_words = index.max_words.max(words.len());
                    let form = Form { words: words.iter().map(|w| w.to_string()).collect(), masks };
                    index.forms.entry(normalised.clone()).or_default().push((at, form));
                    index.map.entry(normalised).or_default().push(at);
                }
            }
        }
        index
    }

    /// Lowercase word with irregular forms mapped to their base.
    pub fn base(&self, word: &str) -> String {
        let lower = word.to_lowercase();
        match self.irregular.get(lower.as_str()).or(self.irregular_nouns.get(lower.as_str())) {
            Some(b) => (*b).to_string(),
            None => lower,
        }
    }

    /// Normalised form of one surface word, as used for lookups.
    pub fn norm(&self, word: &str) -> String {
        self.stemmer.stem(&self.base(word)).into_owned()
    }

    /// Whether `surface` is the key word itself, or an inflection of it that
    /// `mask` allows: a plain suffix, an irregular verb form or plural.
    pub fn word_ok(&self, surface: &str, key: &str, mask: u8) -> bool {
        let surface = surface.to_lowercase();
        if surface == key
            || (mask & IRR_VERB != 0 && self.irregular.get(surface.as_str()) == Some(&key))
            || (mask & IRR_NOUN != 0 && self.irregular_nouns.get(surface.as_str()) == Some(&key))
        {
            return true;
        }
        let suffixes: [(&str, u8); 7] = [
            ("s", PLURAL), ("es", PLURAL), ("ies", PLURAL),
            ("ed", PAST), ("d", PAST), ("ied", PAST), ("ing", ING),
        ];
        for (suf, needs) in suffixes {
            if mask & needs == 0 {
                continue;
            }
            let Some(stem) = surface.strip_suffix(suf) else { continue };
            if stem.is_empty() {
                continue;
            }
            if stem == key || format!("{stem}e") == key {
                return true;
            }
            if let Some(k) = key.strip_suffix('y') {
                if stem == k {
                    return true;
                }
            }
            let mut ch = stem.chars().rev();
            if let (Some(a), Some(b)) = (ch.next(), ch.next()) {
                if a == b && stem[..stem.len() - a.len_utf8()] == *key {
                    return true;
                }
            }
        }
        false
    }

    /// Word-by-word `word_ok` for a phrase form.
    pub fn key_ok(&self, surfaces: &[&str], form: &Form) -> bool {
        form.words.len() == surfaces.len()
            && form
                .words
                .iter()
                .zip(surfaces)
                .zip(&form.masks)
                .all(|((k, s), &mask)| self.word_ok(s, k, mask))
    }
}

/// The English words or phrases an entry can be written as, each with the
/// inflections its words may take: split on ` / `, drop parenthetical notes
/// and a leading `to`. A verb entry drops the `to`; a phrase entry accepts
/// both forms. Only verbs and phrases led by a verb inflect their head;
/// nouns inflect the last word; everything else must match exactly.
fn english_keys(english: &str, kind: &str) -> Vec<(String, Vec<u8>)> {
    let mut keys: Vec<(String, Vec<u8>)> = Vec::new();
    for alt in english.split(" / ") {
        let mut text = String::new();
        let mut depth = 0;
        for c in alt.chars() {
            match c {
                '(' => depth += 1,
                ')' if depth > 0 => depth -= 1,
                _ if depth == 0 => text.push(c),
                _ => {}
            }
        }
        let lower = text.to_lowercase();
        let words: Vec<&str> = lower
            .split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric() && c != '\''))
            .filter(|w| !w.is_empty())
            .collect();
        if words.is_empty() {
            continue;
        }
        let masks_for = |words: &[&str], verb_head: bool| -> Vec<u8> {
            let mut masks = vec![0u8; words.len()];
            if verb_head {
                masks[0] = VERB;
            } else if kind == "noun" {
                masks[words.len() - 1] = NOUN;
            }
            masks
        };
        match words.split_first() {
            Some((&"to", rest)) if (kind == "verb" || kind == "phrase") && !rest.is_empty() => {
                keys.push((rest.join(" "), masks_for(rest, true)));
                if kind == "phrase" {
                    let mut masks = vec![0u8];
                    masks.extend(masks_for(rest, true));
                    keys.push((words.join(" "), masks));
                }
            }
            _ => keys.push((words.join(" "), masks_for(&words, kind == "verb"))),
        }
    }
    keys
}

#[cfg(test)]
mod tests {
    use super::*;


    fn key_strings(english: &str, kind: &str) -> Vec<String> {
        english_keys(english, kind).into_iter().map(|(k, _)| k).collect()
    }

    #[test]
    fn keys_split_alternatives_and_drop_notes() {
        assert_eq!(key_strings("to look for / search", "verb"), ["look for", "search"]);
        assert_eq!(key_strings("to be (essential)", "verb"), ["be"]);
        assert_eq!(key_strings("OK / fine", "phrase"), ["ok", "fine"]);
        assert_eq!(key_strings("rock (music)", "noun"), ["rock"]);
    }

    #[test]
    fn phrase_keys_accept_leading_to_or_not() {
        assert_eq!(key_strings("to bear in mind", "phrase"), ["bear in mind", "to bear in mind"]);
        assert_eq!(
            key_strings("to be up to date / aware", "phrase"),
            ["be up to date", "to be up to date", "aware"]
        );
        assert_eq!(key_strings("in the long run", "phrase"), ["in the long run"]);
        // only the head of a phrase is a verb
        let keys = english_keys("to catch up", "phrase");
        assert_eq!(keys[0].1, [VERB, 0]);
        assert_eq!(keys[1].1, [0, VERB, 0]);
    }

    #[test]
    fn inflection_guard() {
        let ix = Index::parse("");
        assert!(ix.word_ok("windows", "window", NOUN));
        assert!(ix.word_ok("stopped", "stop", VERB));
        assert!(ix.word_ok("running", "run", VERB));
        assert!(ix.word_ok("making", "make", VERB));
        assert!(ix.word_ok("carries", "carry", VERB));
        assert!(ix.word_ok("saw", "see", VERB));
        assert!(!ix.word_ok("openers", "open", VERB));
        assert!(!ix.word_ok("openness", "open", VERB));
        assert!(!ix.word_ok("seen", "saw", VERB));
    }

    #[test]
    fn inflection_depends_on_the_kind() {
        let ix = Index::parse("");
        // verbs take everything, nouns only plurals, the rest nothing
        assert!(ix.word_ok("closes", "close", VERB));
        assert!(ix.word_ok("closed", "close", VERB));
        assert!(ix.word_ok("closing", "close", VERB));
        assert!(ix.word_ok("houses", "house", NOUN));
        assert!(ix.word_ok("cities", "city", NOUN));
        assert!(!ix.word_ok("boxing", "box", NOUN));
        assert!(!ix.word_ok("boxed", "box", NOUN));
        assert!(!ix.word_ok("evening", "even", 0));
        assert!(!ix.word_ok("downs", "down", 0));
        assert!(!ix.word_ok("quickly", "quick", 0));
        assert!(ix.word_ok("down", "down", 0));
        // irregular verb forms map to verbs, irregular plurals to nouns
        assert!(ix.word_ok("went", "go", VERB));
        assert!(ix.word_ok("gone", "go", VERB));
        assert!(!ix.word_ok("went", "go", NOUN));
        assert!(!ix.word_ok("rose", "rise", NOUN));
        assert!(ix.word_ok("children", "child", NOUN));
        assert!(!ix.word_ok("children", "child", VERB));
    }
}
