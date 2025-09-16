use std::env;
use std::fs::{File, read_dir, read_link};
use std::io::{self, Read, Write};
use std::path::PathBuf;

#[derive(Default, Debug, Clone, Copy, PartialEq, Eq)]
struct Flags {
    quiet: bool,
    single: bool,
}

fn print_help(program: &str) {
    println!(
        "Usage: {program} [-q] [-s] [-h] <program name or path>\nOptions:\n  -q    Quiet mode: suppress output, exit 0 if found\n  -s    Single shot: exit after first match\n  -h    Show this help"
    );
}

fn parse_args_from_vec(argv: Vec<String>) -> Result<(Flags, String), i32> {
    let program = argv.first().cloned().unwrap_or_else(|| "fpid".to_string());
    let mut flags = Flags::default();
    let mut target: Option<String> = None;

    let mut i = 1;
    while i < argv.len() {
        let arg = &argv[i];
        if !arg.starts_with('-') || arg == "-" {
            if target.is_some() {
                // Extra positional args -> treat as usage error like C code
                let _ = writeln!(
                    io::stderr(),
                    "Error: Missing program name or path\nUsage: {} [-qhs] <program name or path>",
                    program
                );
                return Err(1);
            }
            target = Some(arg.clone());
            i += 1;
            continue;
        }

        for ch in arg.chars().skip(1) {
            match ch {
                'q' => flags.quiet = true,
                's' => flags.single = true,
                'h' => {
                    print_help(&program);
                    return Err(0);
                }
                _ => {
                    let _ = writeln!(
                        io::stderr(),
                        "Unknown option '{}' (see \"{} -h\")",
                        ch,
                        program
                    );
                    return Err(1);
                }
            }
        }
        i += 1;
    }

    match target {
        Some(t) => Ok((flags, t)),
        None => {
            let _ = writeln!(
                io::stderr(),
                "Error: Missing program name or path\nUsage: {} [-qhs] <program name or path>",
                program
            );
            Err(1)
        }
    }
}

fn is_all_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b: u8| b.is_ascii_digit())
}

fn main() {
    let argv: Vec<String> = env::args().collect();
    let (flags, target) = match parse_args_from_vec(argv) {
        Ok(v) => v,
        Err(code) => std::process::exit(code),
    };

    let is_path = target.contains('/');

    // Scan /proc
    let proc_iter = match read_dir("/proc") {
        Ok(it) => it,
        Err(e) => {
            let _ = writeln!(io::stderr(), "open dir /proc failed: {}", e);
            std::process::exit(1);
        }
    };

    let mut found = false;

    if is_path {
        for entry in proc_iter.flatten() {
            let name = entry.file_name();
            let name_s = match name.to_str() {
                Some(s) if is_all_digits(s) => s,
                _ => continue,
            };

            // Build /proc/<pid>/exe
            let mut exe_path = PathBuf::from("/proc");
            exe_path.push(name_s);
            exe_path.push("exe");

            if let Ok(link_target) = read_link(&exe_path) {
                // Compare exact path string (like C: len equal and memcmp)
                if osstr_eq_str(&link_target, &target) {
                    found = true;
                    if !flags.quiet {
                        println!("{}", name_s);
                    }
                    if flags.single {
                        std::process::exit(0);
                    }
                }
            }
        }
    } else {
        for entry in proc_iter.flatten() {
            let name = entry.file_name();
            let name_s = match name.to_str() {
                Some(s) if is_all_digits(s) => s,
                _ => continue,
            };

            // Build /proc/<pid>/cmdline
            let mut cmd_path = PathBuf::from("/proc");
            cmd_path.push(name_s);
            cmd_path.push("cmdline");

            // Read cmdline as bytes, since it is NUL-separated
            let mut f = match File::open(&cmd_path) {
                Ok(f) => f,
                Err(_) => continue,
            };
            let mut buf = Vec::with_capacity(4096);
            if f.read_to_end(&mut buf).is_err() || buf.is_empty() {
                continue;
            }

            // First arg up to first NUL is argv[0]
            let first = match buf.split(|b| *b == 0).next() {
                Some(v) => v,
                None => continue,
            };

            // Get basename of argv[0]
            let base = match first.rsplit(|b| *b == b'/').next() {
                Some(v) => v,
                None => first,
            };

            if base.len() == target.len() && bytes_eq_ascii(base, target.as_bytes()) {
                found = true;
                if !flags.quiet {
                    println!("{}", name_s);
                }
                if flags.single {
                    std::process::exit(0);
                }
            }
        }
    }

    std::process::exit(if found { 0 } else { 1 });
}

fn osstr_eq_str(path: &std::path::Path, s: &str) -> bool {
    // Compare raw bytes of OsStr to the target str bytes exactly
    // This mirrors the C code's exact length + memcmp behavior.
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let os_bytes = path.as_os_str().as_bytes();
        os_bytes == s.as_bytes()
    }
    #[cfg(not(unix))]
    {
        // On non-unix, fallback to string compare which may not be exact on Windows.
        // But this tool targets Linux /proc.
        path.to_string_lossy() == s
    }
}

fn bytes_eq_ascii(a: &[u8], b: &[u8]) -> bool {
    a == b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_help_returns_code0() {
        let argv = vec!["fpid".to_string(), "-h".to_string()];
        let res = parse_args_from_vec(argv);
        assert!(matches!(res, Err(0)));
    }

    #[test]
    fn test_missing_target() {
        let argv = vec!["fpid".to_string()];
        let res = parse_args_from_vec(argv);
        assert!(matches!(res, Err(1)));
    }

    #[test]
    fn test_parse_flags_and_target() {
        let argv = vec!["fpid".to_string(), "-qs".to_string(), "sshd".to_string()];
        let (flags, target) = parse_args_from_vec(argv).unwrap();
        assert_eq!(
            flags,
            Flags {
                quiet: true,
                single: true
            }
        );
        assert_eq!(target, "sshd");
    }

    #[test]
    fn test_unknown_option() {
        let argv = vec!["fpid".to_string(), "-x".to_string()];
        let res = parse_args_from_vec(argv);
        assert!(matches!(res, Err(1)));
    }

    #[test]
    fn test_extra_positional_error() {
        let argv = vec![
            "fpid".to_string(),
            "-q".to_string(),
            "sshd".to_string(),
            "extra".to_string(),
        ];
        let res = parse_args_from_vec(argv);
        assert!(matches!(res, Err(1)));
    }
}
