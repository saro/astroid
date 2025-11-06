# Security Audit Report for Astroid Mail Client

**Date**: November 6, 2025
**Version Audited**: 0.17.0
**Branch**: claude/performance-enhancement-011CUrPH7PsCs4FDr2HE77ue
**Auditor**: Security Assessment Tool

---

## Executive Summary

This security audit analyzed the Astroid email client codebase for common security vulnerabilities, coding issues, and best practice violations. Astroid is a C++17-based email client using GTK+, WebKit, GMime, and notmuch as a backend.

**Overall Security Posture**: MODERATE

The codebase shows reasonable security practices in many areas but has several findings that warrant attention, particularly around command execution, plugin security, and input validation.

---

## Project Overview

- **Language**: C++17
- **Build System**: CMake
- **Lines of Code**: ~103 source files
- **Key Dependencies**:
  - GTK+ 3.10+
  - WebKit2GTK 4.0/4.1
  - GMime 2.6/3.0
  - Boost (filesystem, program_options, log, thread, date_time)
  - Notmuch
  - libsass
  - Protobuf 3.0+
  - libpeas (for plugins)

---

## Security Findings

### CRITICAL SEVERITY

None identified.

### HIGH SEVERITY

#### H-1: Command Injection Risk via External Command Execution

**Location**:
- `src/utils/cmd.cc:53`
- `src/utils/cmd.cc:95-106`
- `src/modes/editor/external.cc:36-47`
- `src/modes/editor/plugin.cc:63`
- `src/compose_message.cc:185-230`
- `src/poll.cc:188-222`

**Description**: The application uses `Glib::spawn_command_line_sync`, `Glib::spawn_async_with_pipes`, and `Glib::spawn_command_line_async` to execute external commands. While `Glib::shell_parse_argv` is used in some places (which is safer than direct shell execution), the commands are constructed from user-configurable settings and potentially user input.

**Evidence**:
```cpp
// src/utils/cmd.cc:53
Glib::spawn_command_line_sync (_cmd, &_stdout, &_stderr, &exit);

// src/utils/cmd.cc:95
std::vector<std::string> args = Glib::shell_parse_argv (cmd);
Glib::spawn_async_with_pipes ("", args, ...);

// src/modes/editor/external.cc:30-36
ustring cmd = ustring::compose (editor_cmd, em->tmpfile_path.c_str ());
auto args = Glib::shell_parse_argv (cmd);
Glib::spawn_async_with_pipes ("", args, ...);
```

**Risk**: If user-controlled input (email addresses, file paths, configuration values) is not properly sanitized before being passed to these commands, attackers could inject malicious commands.

**Recommendation**:
1. Never pass user input directly to command execution functions
2. Use argument arrays instead of shell parsing where possible
3. Implement strict input validation and sanitization
4. Use allowlists for acceptable command patterns
5. Consider using `Glib::SPAWN_SEARCH_PATH` without shell interpretation

---

#### H-2: Insecure Temporary File Creation

**Location**:
- `src/modes/edit_message.cc:1322-1339`

**Description**: Temporary files are created using predictable names based on message IDs without using secure temporary file creation functions.

**Evidence**:
```cpp
void EditMessage::make_tmpfile () {
  tmpfile_path = tmpfile_path / path(msg_id);  // Predictable path
  LOG (info) << "em: tmpfile: " << tmpfile_path;

  if (is_regular_file (tmpfile_path)) {
    LOG (error) << "em: error: tmpfile already exists!";
    throw runtime_error ("em: tmpfile already exists!");
  }

  tmpfile.open (tmpfile_path.c_str(), std::fstream::out);
}
```

**Risk**:
- Race condition between check and use (TOCTOU)
- Predictable filenames could allow local attackers to:
  - Read sensitive email content
  - Replace file contents (symlink attacks)
  - Denial of service

**Recommendation**:
1. Use `mkstemp()` or Boost equivalent for secure temporary file creation
2. Set restrictive permissions (0600) on temporary files
3. Ensure temporary files are deleted on exit
4. Use atomic file operations

---

#### H-3: Plugin Security - Unrestricted Plugin Loading

**Location**:
- `src/plugin/manager.cc:40-129`

**Description**: The plugin system uses libpeas to load Python3 and Lua5.1 plugins from user-specified directories without apparent sandboxing or permission restrictions.

**Evidence**:
```cpp
peas_engine_enable_loader (engine, "python3");
peas_engine_enable_loader (engine, "lua5.1");

bfs::path plugin_dir = astroid->standard_paths().plugin_dir;
peas_engine_prepend_search_path (engine, plugin_dir.c_str (), NULL);

// Plugins loaded automatically
peas_engine_load_plugin (engine, p);
```

**Risk**:
- Malicious plugins can execute arbitrary code with user privileges
- No apparent sandboxing or capability restrictions
- Plugins have access to sensitive email data
- Could be used for data exfiltration

**Recommendation**:
1. Implement plugin signature verification
2. Add plugin permission system (similar to Android)
3. Sandbox plugin execution environment
4. Document plugin security model for users
5. Add plugin allowlist/blocklist functionality
6. Warn users about plugin installation risks

---

### MEDIUM SEVERITY

#### M-1: Cryptographic Implementation Concerns

**Location**:
- `src/crypto.cc:287-317`
- `src/crypto.cc:319-327`

**Description**: The code uses MD5 for checksums and relies on GMime/GPG for encryption, but has an `always_trust` option that bypasses key validation.

**Evidence**:
```cpp
bool always_trust = config.get<bool> ("gpg.always_trust");

// Later used in encryption
GMIME_ENCRYPT_ALWAYS_TRUST : GMIME_ENCRYPT_NONE

// MD5 usage
ustring Crypto::get_md5_digest (ustring str) {
  std::string cs = Glib::Checksum::compute_checksum (
    Glib::Checksum::ChecksumType::CHECKSUM_MD5, str);
  return cs;
}
```

**Risk**:
- MD5 is cryptographically broken and should not be used for security purposes
- `always_trust` option could lead to accepting invalid/malicious keys
- Users may encrypt to wrong recipients if keys aren't validated

**Recommendation**:
1. Replace MD5 with SHA-256 or SHA-3 for checksums
2. Clearly document security implications of `always_trust`
3. Warn users when using `always_trust` mode
4. Implement key fingerprint verification UI
5. Consider using libsodium for crypto operations

---

#### M-2: Information Disclosure via Logging

**Location**:
- Throughout codebase (various LOG statements)

**Description**: Extensive debug logging may leak sensitive information including email content, paths, and configuration details.

**Evidence**:
```cpp
LOG (debug) << "cr: encrypted for: " << nm << "(" << em << ") [" << fp << "] [" << key << "]";
LOG (info) << "em: tmpfile: " << tmpfile_path;
LOG (debug) << "em: ex: launching editor: " << cmd;
```

**Risk**:
- Log files may contain sensitive email data
- Paths and commands logged could reveal system information
- Debug logs might be enabled in production builds

**Recommendation**:
1. Implement log level controls
2. Sanitize sensitive data before logging
3. Ensure debug logs are disabled in release builds
4. Document log file locations and security implications
5. Consider log file encryption for sensitive environments

---

#### M-3: Weak Random Number Generation

**Location**:
- No strong random number generation found

**Description**: The code does not appear to use `rand()` or `srand()` (which is good), but there's no evidence of cryptographically secure random number generation for operations that might require it (e.g., generating message IDs).

**Recommendation**:
1. Use `/dev/urandom` or `std::random_device` for random values
2. Never use `rand()` for security-sensitive operations
3. Ensure message IDs have sufficient entropy

---

#### M-4: Environment Variable Manipulation

**Location**:
- `src/plugin/manager.cc:54-64`

**Description**: The plugin manager modifies environment variables which could affect child processes.

**Evidence**:
```cpp
setenv ("GI_TYPELIB_PATH", bfs::current_path ().c_str (), 1);
setenv ("ASTROID_CONFIG", astroid->standard_paths().config_file.c_str (), 1);
```

**Risk**:
- Environment pollution for child processes
- Could interfere with other applications
- Potential information disclosure

**Recommendation**:
1. Minimize environment variable modifications
2. Document all environment variables set by the application
3. Consider using process-local configuration instead

---

### LOW SEVERITY

#### L-1: Missing Buffer Overflow Protections

**Description**: The code uses C++17 with STL containers which provides memory safety, but also interfaces with C libraries (GMime, GTK+). No unsafe C string functions (strcpy, sprintf, etc.) were found in the codebase, which is excellent.

**Status**: GOOD - No unsafe string functions detected

**Recommendation**:
1. Continue using C++ standard library containers
2. Ensure all C library interfaces are wrapped safely
3. Enable compiler hardening flags (-fstack-protector-strong, -D_FORTIFY_SOURCE=2)

---

#### L-2: File Permission Controls

**Location**:
- No explicit file permission settings found

**Description**: No explicit permission settings (chmod) were found in the code. File permissions likely depend on umask.

**Recommendation**:
1. Set explicit permissions on sensitive files (config, cache, temp files)
2. Ensure temporary files are created with 0600 permissions
3. Verify runtime directory permissions

---

#### L-3: Error Handling

**Description**: Error handling appears to be implemented using exceptions and return value checks. Some error messages may leak information.

**Recommendation**:
1. Review all error messages for information disclosure
2. Implement consistent error handling patterns
3. Ensure resources are freed in error paths

---

## Positive Security Practices Observed

1. **No hardcoded credentials**: No passwords, API keys, or secrets found
2. **Modern C++**: Uses C++17 with STL, avoiding unsafe C functions
3. **No unsafe string operations**: No strcpy, strcat, sprintf, gets found
4. **Use of established libraries**: Relies on well-maintained libraries (GMime, GTK+, Boost)
5. **Input parsing**: Uses Glib::shell_parse_argv instead of direct shell execution in most places
6. **Clean git hooks**: No malicious code in git hooks
7. **Test infrastructure**: Includes test framework and test data

---

## Dependency Analysis

### Current Dependencies

All dependencies are system-managed through pkg-config:
- GTK+ 3.10+
- WebKit2GTK 2.22+ (4.0 or 4.1)
- GMime (version matched to Notmuch)
- Boost libraries
- Protobuf 3.0+
- Notmuch
- libsass
- libpeas (for plugins)

### Recommendations

1. **Regular Updates**: Ensure all dependencies are kept up-to-date
2. **Vulnerability Monitoring**: Subscribe to security advisories for:
   - WebKit (known for security issues)
   - GMime (handles untrusted email data)
   - Protobuf
3. **Version Pinning**: Consider minimum secure versions for dependencies
4. **Supply Chain**: Verify package signatures when installing dependencies

---

## WebKit Security Considerations

**Risk Level**: HIGH

WebKit is used to render HTML emails and is a common attack vector. The code includes:

**Location**: `src/modes/thread_view/thread_view.cc`, webextension files

**Concerns**:
1. HTML email rendering can execute JavaScript (unless disabled)
2. WebKit vulnerabilities are common and actively exploited
3. Email content is untrusted input from potentially malicious senders

**Mitigations Observed**:
- Web extension for content isolation
- Protocol handling (ae_protocol.cc)

**Recommendations**:
1. Ensure JavaScript is disabled for email rendering
2. Implement Content Security Policy (CSP)
3. Sandbox WebKit processes
4. Keep WebKit updated to latest version
5. Consider using a minimal HTML renderer instead of full WebKit
6. Implement URL filtering for external resources
7. Add user warnings before loading external content

---

## Configuration Security

**Location**: `src/config.cc`

The configuration system uses JSON format and reads from user-specified paths.

**Observations**:
- Uses XDG Base Directory Specification (good practice)
- Configuration in `~/.config/astroid/config`
- No input validation observed for config values

**Recommendations**:
1. Validate all configuration values
2. Set safe defaults for security-sensitive options
3. Implement configuration schema validation
4. Protect config files with appropriate permissions (0600)
5. Document security implications of config options

---

## Testing and Code Quality

**Test Coverage**: Present but coverage unknown
**Static Analysis**: Not observed in build system
**Compiler Warnings**: `-Wall` enabled, `-Wextra` in debug builds

**Recommendations**:
1. Add static analysis tools (cppcheck, clang-tidy)
2. Enable address sanitizer (ASAN) in test builds
3. Add fuzzing for parser code (email parsing, HTML rendering)
4. Increase compiler warning levels
5. Add security-focused test cases

---

## Recommendations Summary

### Immediate Actions (High Priority)

1. **Fix temporary file creation** (H-2)
   - Use secure temp file creation APIs
   - Set proper permissions (0600)

2. **Review command execution** (H-1)
   - Audit all `Glib::spawn*` calls
   - Implement input sanitization
   - Use argument arrays instead of shell parsing

3. **Document plugin security** (H-3)
   - Add warnings about plugin risks
   - Consider plugin sandboxing

### Short Term (Medium Priority)

4. **Replace MD5** (M-1)
   - Switch to SHA-256 for checksums
   - Document crypto options

5. **Review logging** (M-2)
   - Implement log levels
   - Sanitize sensitive data in logs

6. **WebKit hardening**
   - Disable JavaScript in email rendering
   - Implement CSP
   - Update WebKit regularly

### Long Term (Low Priority)

7. **Add security testing**
   - Implement fuzzing
   - Add ASAN/UBSAN
   - Static analysis integration

8. **Code audits**
   - Regular security reviews
   - Dependency vulnerability scanning
   - Penetration testing

9. **Security documentation**
   - Document security model
   - Create security.md
   - Publish security policy

---

## Compliance and Standards

### Relevant Standards

- **CWE Top 25**: Review against most dangerous software weaknesses
- **OWASP C++ Secure Coding**: Follow C++ security guidelines
- **Email Security**: RFC compliance, handling of malicious emails

### Privacy Considerations

Email clients handle sensitive personal data:
- Email content
- Contact information
- Encryption keys
- Configuration data

**Recommendations**:
- Clear data retention policies
- Secure deletion of sensitive data
- Privacy policy documentation
- GDPR compliance (if applicable)

---

## Conclusion

The Astroid email client demonstrates reasonable security practices in many areas, particularly in avoiding common C/C++ pitfalls like buffer overflows and unsafe string operations. However, several areas require attention:

1. **Command execution security** needs immediate review
2. **Plugin system** needs security hardening
3. **Temporary file handling** should use secure APIs
4. **WebKit integration** requires additional hardening
5. **Dependency management** should include security monitoring

The codebase is well-structured and uses modern C++ practices, which provides a good foundation for security improvements. Implementing the recommendations in this report would significantly improve the security posture of the application.

**Overall Risk Assessment**: MODERATE
**Recommended Actions**: Address High severity findings within 30 days

---

## Appendix A: Files Reviewed

- Build system: CMakeLists.txt
- Core source files: src/*.cc, src/*.hh
- Crypto implementation: src/crypto.cc
- Plugin system: src/plugin/*.cc
- Command execution: src/utils/cmd.cc
- Editor integration: src/modes/editor/*.cc
- Configuration: src/config.cc
- Tests: tests/*.cc

**Total Files Analyzed**: ~103 source files
**Analysis Date**: November 6, 2025

---

## Appendix B: Tools and Methodology

### Tools Used
- Static code analysis (grep, pattern matching)
- Dependency review
- Manual code review
- Security pattern detection

### Methodology
1. Reconnaissance and technology stack identification
2. Secrets and credentials scanning
3. Common vulnerability patterns (injection, XSS, etc.)
4. Cryptography review
5. Input validation analysis
6. Memory safety review
7. Plugin security assessment
8. Configuration security review
9. Build system security analysis

---

*End of Security Audit Report*
