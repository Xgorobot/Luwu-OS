cmake_minimum_required(VERSION 3.16)
project(luwu_launcher LANGUAGES CXX)

set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

qt_add_executable(luwu_launcher
    main.cpp
    galleryview.cpp
    galleryview.h
    demogridview.cpp
    demogridview.h
    keyfilter.h
    statusbar.cpp
    statusbar.h
    i18n.cpp
    i18n.h
)
target_link_libraries(luwu_launcher PRIVATE Qt6::Widgets)
