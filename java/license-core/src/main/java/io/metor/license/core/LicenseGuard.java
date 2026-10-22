package io.metor.license.core;

/** Local license validation; product code chooses which entry points require it. */
@FunctionalInterface
public interface LicenseGuard {
    void requireValid();
}
